"""A SUPPORT-only member of a workbench-only program, through the enricher and Cedar.

The enricher reduces the member's roles, the resolver builds the entities from the reduced assignment
and Cedar decides, so a reduction that hands out PLATFORM_USER shows up as an allowed user action.
"""

from unittest import mock

import cedarpy
import pytest

from app.authorization.domain.ports import assignments_query_service
from app.authorization.domain.read_models import project_assignment, project_settings
from app.authorization.domain.services.auth import authorizer, authorizer_steps
from infra.tests.test_support_role_cedar import PROJECT, PROVISIONING, USER, _to_cedar_value

USER_ACTIONS = ["LaunchProduct", "GetAvailableProducts", "GetProvisionedProductSSHKey", "StopProvisionedProduct"]
SUPPORT_ACTIONS = ["GetProjectPaginatedProvisionedProducts", "GetProjectProvisionedProducts"]


def _evaluate(action: str, roles: list[str], remote_support_enabled: bool) -> tuple[bool, list[str]]:
    qs = mock.create_autospec(spec=assignments_query_service.AssignmentsQueryService, instance=True)
    qs.get_user_assignments.return_value = [project_assignment.Assignment(userId=USER, projectId=PROJECT, roles=roles)]
    qs.get_group_assignments.return_value = []
    qs.get_project_settings.return_value = project_settings.ProjectSettings(
        projectId=PROJECT, experience="workbench-only", remoteSupportEnabled=remote_support_enabled
    )
    context = authorizer.AuthorizationContext(
        user_name=USER,
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context="provisioning"),
        project_scoped_bounded_contexts=["provisioning"],
    )
    request = authorizer.AuthorizationRequest(
        auth_token="t",
        api_id="api",
        operation_id=action,
        resource_ids={"projectId": PROJECT},
        resource_path="/projects/{projectId}/products/available",
        resource="r",
    )
    assert authorizer_steps.ProjectsBCContextEnricher(assignments_query_service=qs).invoke(request, context)

    resolved = authorizer_steps.AVPEntityResolutionContext(
        entities=[
            authorizer_steps.AVPEntity(
                identifier=authorizer_steps.AVPEntityIdentifier(entityId=USER, entityType="VEW::User")
            )
        ]
    )
    authorizer_steps.VEWProjectAssignmentEntityResolver().resolve(
        request=request, context=context, avp_entities=resolved
    )
    entities = [
        {
            "uid": {"type": e["identifier"]["entityType"], "id": e["identifier"]["entityId"]},
            "attrs": {n: _to_cedar_value(v) for n, v in e["attributes"].items()},
            "parents": [{"type": p["entityType"], "id": p["entityId"]} for p in e["parents"]],
        }
        for e in (x.model_dump(exclude_none=True, by_alias=True) for x in resolved.entities)
    ]
    policies, schema = PROVISIONING
    result = cedarpy.is_authorized(
        request={
            "principal": f'VEW::User::"{USER}"',
            "action": f'VEW::Action::"{action}"',
            "resource": f'VEW::Project::"{PROJECT}"',
            "context": {},
        },
        policies=policies,
        entities=entities,
        schema=schema,
    )
    assert not result.diagnostics.errors, result.diagnostics.errors
    return result.allowed, sorted(context.roles)


@pytest.mark.parametrize("remote_support_enabled", [True, False])
@pytest.mark.parametrize("action", USER_ACTIONS)
def test_support_only_member_gets_no_user_rights(action, remote_support_enabled):
    allowed, roles = _evaluate(action, ["SUPPORT"], remote_support_enabled)

    assert roles == ["SUPPORT"]
    assert not allowed


@pytest.mark.parametrize("action", SUPPORT_ACTIONS)
def test_support_only_member_still_supports_when_allowed(action):
    assert _evaluate(action, ["SUPPORT"], True)[0]
    assert not _evaluate(action, ["SUPPORT"], False)[0]


@pytest.mark.parametrize(
    "roles, expected",
    [
        (["POWER_USER"], ["PLATFORM_USER"]),
        (["PROGRAM_OWNER"], ["PLATFORM_USER", "PROGRAM_OWNER"]),
        (["PROGRAM_OWNER", "SUPPORT"], ["PLATFORM_USER", "PROGRAM_OWNER", "SUPPORT"]),
        (["BETA_USER", "SUPPORT"], ["PLATFORM_USER", "SUPPORT"]),
        (["PRODUCT_CONTRIBUTOR"], ["PLATFORM_USER"]),
        (["ADMIN"], ["ADMIN"]),
    ],
)
def test_other_members_keep_user_access(roles, expected):
    allowed, effective = _evaluate("LaunchProduct", roles, False)

    assert effective == expected
    assert allowed
