"""The project-less (platform-wide) actions are for platform admins only, evaluated with Cedar.

These policies checked `totalAdminAssignments > 0`, so ADMIN on any single project let a user create projects,
read every spoke's capacity and request quota in any spoke. Now the authorizer marks a platform admin (a member
of a platform-admin group) with `platformAdmin`, and only that counts.

The whole path the authorizer takes: ProjectsBCContextEnricher, the entity resolver, then the policies and
schemas from infra/auth, for a request without a project in the path.
"""

from unittest import mock

import cedarpy
import pytest

from app.authorization.domain.ports import assignments_query_service
from app.authorization.domain.read_models import project_assignment, project_settings
from app.authorization.domain.services.auth import authorizer, authorizer_steps
from infra.auth import projects_auth, projects_auth_schema, provisioning_auth, provisioning_auth_schema

USER = "USER-1"
PLATFORM_ADMINS = "6ef5c9f6-de67-4103-8695-6e70d688794a"
OTHER_GROUP = "11111111-1111-1111-1111-111111111111"

PROVISIONING = (
    "provisioning",
    "\n".join(p.statement for p in provisioning_auth.provisioning_bc_auth_policies),
    provisioning_auth_schema.provisioning_schema,
)
PROJECTS = (
    "projects",
    "\n".join(p.statement for p in projects_auth.projects_bc_auth_policies),
    projects_auth_schema.projects_schema,
)

PLATFORM_WIDE_ACTIONS = [
    (PROVISIONING, "GetCapacityOverview", "/capacity"),
    (PROVISIONING, "RequestQuotaIncrease", "/capacity/accounts/{awsAccountId}/quotas/{quotaCode}/increase"),
    (PROJECTS, "CreateProject", "/projects"),
]


def _to_cedar_value(value: dict):
    if "entityIdentifier" in value:
        ident = value["entityIdentifier"]
        return {"__entity": {"type": ident["entityType"], "id": ident["entityId"]}}
    if "boolean" in value:
        return value["boolean"]
    if "long" in value:
        return value["long"]
    raise ValueError(f"unsupported attribute value {value}")


def _allowed(bc, action: str, path: str, user: str, groups: list[str], roles: list[str] | None) -> bool:
    bounded_context, policies, schema = bc
    qs = mock.create_autospec(spec=assignments_query_service.AssignmentsQueryService, instance=True)
    qs.get_user_assignments.return_value = (
        [project_assignment.Assignment(userId=user, projectId="proj-team1", roles=roles)] if roles else []
    )
    qs.get_group_assignments.return_value = []
    qs.get_project_settings.return_value = project_settings.ProjectSettings(projectId="proj-team1")
    context = authorizer.AuthorizationContext(
        user_name=user,
        user_email="someone@example.com",
        trusted_group_ids=groups,
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context=bounded_context),
        project_scoped_bounded_contexts=[bounded_context],
    )
    request = authorizer.AuthorizationRequest(
        auth_token="t", api_id="api", operation_id="any", resource_ids={}, resource_path=path, resource="r"
    )
    enricher = authorizer_steps.ProjectsBCContextEnricher(
        assignments_query_service=qs, platform_admin_groups=[PLATFORM_ADMINS]
    )
    assert enricher.invoke(request, context)
    resolved = authorizer_steps.AVPEntityResolutionContext(
        entities=[
            authorizer_steps.AVPEntity(identifier=authorizer_steps.AVPEntityIdentifier(entityId=user, entityType="VEW::User"))
        ]
    )
    authorizer_steps.VEWProjectAssignmentEntityResolver().resolve(request=request, context=context, avp_entities=resolved)
    wire = [e.model_dump(exclude_none=True, by_alias=True) for e in resolved.entities]
    entities = [
        {
            "uid": {"type": e["identifier"]["entityType"], "id": e["identifier"]["entityId"]},
            "attrs": {name: _to_cedar_value(value) for name, value in e["attributes"].items()},
            "parents": [{"type": p["entityType"], "id": p["entityId"]} for p in e["parents"]],
        }
        for e in wire
    ]
    result = cedarpy.is_authorized(
        request={
            "principal": f'VEW::User::"{user}"',
            "action": f'VEW::Action::"{action}"',
            "resource": 'VEW::Project::"platform"',
            "context": {},
        },
        policies=policies,
        entities=entities,
        schema=schema,
    )
    assert not result.diagnostics.errors, result.diagnostics.errors
    return result.allowed


IDS = [f"{a}" for _, a, _ in PLATFORM_WIDE_ACTIONS]


@pytest.mark.parametrize("bc,action,path", PLATFORM_WIDE_ACTIONS, ids=IDS)
def test_a_platform_admin_group_member_reaches_the_platform_wide_actions(bc, action, path):
    assert _allowed(bc, action, path, USER, [PLATFORM_ADMINS], None)


@pytest.mark.parametrize("bc,action,path", PLATFORM_WIDE_ACTIONS, ids=IDS)
@pytest.mark.parametrize("roles", [["ADMIN"], ["PROGRAM_OWNER"], ["PLATFORM_USER"], ["SUPPORT"], None])
def test_a_role_in_one_project_is_not_platform_wide(bc, action, path, roles):
    """ADMIN on one project used to count (totalAdminAssignments > 0): it no longer does."""
    assert not _allowed(bc, action, path, USER, [OTHER_GROUP], roles)
