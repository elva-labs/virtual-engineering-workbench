"""The SUPPORT role and the per-project remote-support switch, evaluated with Cedar.

Entities come from the authorizer's own resolver (what Amazon Verified Permissions receives), the
policies and schemas from infra/auth - so this checks the three together, not the policy text.
"""

import cedarpy
import pytest

from app.authorization.domain.read_models import project_assignment
from app.authorization.domain.services.auth import authorizer, authorizer_steps
from infra.auth import (
    packaging_auth,
    packaging_auth_schema,
    projects_auth,
    projects_auth_schema,
    provisioning_auth,
    provisioning_auth_schema,
    publishing_auth,
    publishing_auth_schema,
)

PROJECT = "proj-test1"
USER = "USER-1"


def _to_cedar_value(value: dict):
    if "entityIdentifier" in value:
        ident = value["entityIdentifier"]
        return {"__entity": {"type": ident["entityType"], "id": ident["entityId"]}}
    if "boolean" in value:
        return value["boolean"]
    if "long" in value:
        return value["long"]
    raise ValueError(f"unsupported attribute value {value}")


def _entities(roles: list[str], remote_support_enabled: bool, bounded_context: str) -> list[dict]:
    """The entity list the authorizer sends to Verified Permissions, in cedarpy's format."""
    context = authorizer.AuthorizationContext(
        user_name=USER,
        project_assignments=[project_assignment.Assignment(userId=USER, projectId=PROJECT, roles=roles)],
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context=bounded_context),
        project_scoped_bounded_contexts=[bounded_context],
        remote_support_enabled=remote_support_enabled,
    )
    request = authorizer.AuthorizationRequest(
        auth_token="t",
        api_id="api",
        operation_id="any",
        resource_ids={"projectId": PROJECT},
        resource_path="/projects/{projectId}",
        resource="r",
    )
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

    wire = [e.model_dump(exclude_none=True, by_alias=True) for e in resolved.entities]
    return [
        {
            "uid": {"type": e["identifier"]["entityType"], "id": e["identifier"]["entityId"]},
            "attrs": {name: _to_cedar_value(value) for name, value in e["attributes"].items()},
            "parents": [{"type": p["entityType"], "id": p["entityId"]} for p in e["parents"]],
        }
        for e in wire
    ]


def _policies(policies) -> str:
    return "\n".join(policy.statement for policy in policies)


PROVISIONING = (
    _policies(provisioning_auth.provisioning_bc_auth_policies),
    provisioning_auth_schema.provisioning_schema,
)
PROJECTS = (_policies(projects_auth.projects_bc_auth_policies), projects_auth_schema.projects_schema)


def _allowed(bc, action: str, roles: list[str], remote_support_enabled: bool = True) -> bool:
    policies, schema = bc
    result = cedarpy.is_authorized(
        request={
            "principal": f'VEW::User::"{USER}"',
            "action": f'VEW::Action::"{action}"',
            "resource": f'VEW::Project::"{PROJECT}"',
            "context": {},
        },
        policies=policies,
        entities=_entities(roles, remote_support_enabled, "provisioning" if bc is PROVISIONING else "projects"),
        schema=schema,
    )
    assert not result.diagnostics.errors, result.diagnostics.errors
    return result.allowed


@pytest.mark.parametrize(
    "policies,schema",
    [
        PROVISIONING,
        PROJECTS,
        (_policies(packaging_auth.packaging_bc_auth_policies), packaging_auth_schema.packaging_schema),
        (_policies(publishing_auth.publishing_bc_auth_policies), publishing_auth_schema.publishing_schema),
    ],
    ids=["provisioning", "projects", "packaging", "publishing"],
)
def test_policies_validate_against_their_schema(policies, schema):
    result = cedarpy.validate_policies(policies, schema)
    assert result.validation_passed, result.errors


SUPPORT_ACTIONS = ["GetProjectPaginatedProvisionedProducts", "GetProjectProvisionedProducts"]


@pytest.mark.parametrize("action", SUPPORT_ACTIONS)
def test_support_may_support_while_the_project_allows_it(action):
    assert _allowed(PROVISIONING, action, ["SUPPORT"])


@pytest.mark.parametrize("action", SUPPORT_ACTIONS)
def test_support_may_not_support_when_the_project_disallows_it(action):
    assert not _allowed(PROVISIONING, action, ["SUPPORT"], remote_support_enabled=False)


@pytest.mark.parametrize("action", ["LaunchProduct", "GetProvisionedProductSSHKey", "StopProvisionedProduct"])
def test_support_gets_no_user_rights(action):
    assert not _allowed(PROVISIONING, action, ["SUPPORT"])


def test_admin_includes_support_while_allowed():
    assert _allowed(PROVISIONING, "GetProjectProvisionedProducts", ["ADMIN"])


def test_platform_users_do_not_list_other_users_workbenches():
    assert not _allowed(PROVISIONING, "GetProjectProvisionedProducts", ["PLATFORM_USER"])


def test_support_sees_the_supported_project_but_cannot_change_it():
    assert _allowed(PROJECTS, "GetProject", ["SUPPORT"])
    assert not _allowed(PROJECTS, "GetProject", ["SUPPORT"], remote_support_enabled=False)
    assert not _allowed(PROJECTS, "UpdateProject", ["SUPPORT"])
    assert not _allowed(PROJECTS, "AddProjectUser", ["SUPPORT"])


def test_existing_rights_are_unchanged_when_remote_support_is_off():
    assert _allowed(PROVISIONING, "LaunchProduct", ["PLATFORM_USER"], remote_support_enabled=False)
    assert _allowed(PROVISIONING, "GetProjectProvisionedProducts", ["PROGRAM_OWNER"], remote_support_enabled=False)
    assert _allowed(PROJECTS, "UpdateProject", ["ADMIN"], remote_support_enabled=False)
