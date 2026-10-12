"""Shutdown-policy rights, evaluated against actual policies and authorizer-generated entities."""

import cedarpy
import pytest

from app.authorization.domain.read_models.project_assignment import Assignment
from app.authorization.domain.services.auth import authorizer, authorizer_steps
from infra.auth import projects_auth, projects_auth_schema
from infra.tests.test_support_role_cedar import _to_cedar_value

USER = "USER-1"
PROJECT = "company-a"
OTHER_PROJECT = "company-b"
POLICIES = "\n".join(policy.statement for policy in projects_auth.projects_bc_auth_policies)
READ = "GetProjectWorkbenchLifecycle"
WRITES = ["UpdateProjectWorkbenchLifecycle", "DeleteProjectWorkbenchLifecycle"]


def _allowed(action, roles, assignment_project=PROJECT, target_project=PROJECT, remote_support_enabled=True):
    context = authorizer.AuthorizationContext(
        user_name=USER,
        project_assignments=[
            Assignment(userId=USER, projectId=assignment_project, roles=roles),
            *(
                [Assignment(userId=USER, projectId=target_project, roles=["PLATFORM_USER"])]
                if assignment_project != target_project
                else []
            ),
        ],
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context="projects"),
        project_scoped_bounded_contexts=["projects"],
        remote_support_enabled=remote_support_enabled,
    )
    request = authorizer.AuthorizationRequest(
        auth_token="trusted-token",
        api_id="api",
        operation_id=action,
        resource_ids={"projectId": target_project},
        resource_path="/projects/{projectId}/workbench-lifecycle",
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
    entities = []
    for entity in resolved.entities:
        wire = entity.model_dump(exclude_none=True, by_alias=True)
        entities.append(
            {
                "uid": {"type": wire["identifier"]["entityType"], "id": wire["identifier"]["entityId"]},
                "attrs": {name: _to_cedar_value(value) for name, value in wire["attributes"].items()},
                "parents": [{"type": parent["entityType"], "id": parent["entityId"]} for parent in wire["parents"]],
            }
        )
    result = cedarpy.is_authorized(
        request={
            "principal": f'VEW::User::"{USER}"',
            "action": f'VEW::Action::"{action}"',
            "resource": f'VEW::Project::"{target_project}"',
            "context": {},
        },
        policies=POLICIES,
        entities=entities,
        schema=projects_auth_schema.projects_schema,
    )
    assert not result.diagnostics.errors, result.diagnostics.errors
    return result.allowed


def test_policies_and_actions_validate():
    result = cedarpy.validate_policies(POLICIES, projects_auth_schema.projects_schema)
    assert result.validation_passed, result.errors


@pytest.mark.parametrize("role", ["ADMIN", "PROGRAM_OWNER"])
@pytest.mark.parametrize("action", [READ, *WRITES])
def test_project_managers_may_read_and_write(role, action):
    assert _allowed(action, [role])


@pytest.mark.parametrize("role", ["PLATFORM_USER", "POWER_USER", "PRODUCT_CONTRIBUTOR", "BETA_USER", "SUPPORT"])
@pytest.mark.parametrize("action", WRITES)
def test_ordinary_members_and_support_cannot_write(role, action):
    assert not _allowed(action, [role])


@pytest.mark.parametrize("role", ["ADMIN", "PROGRAM_OWNER"])
@pytest.mark.parametrize("action", WRITES)
def test_manager_in_company_a_cannot_write_company_b(role, action):
    assert not _allowed(action, [role], target_project=OTHER_PROJECT)
    assert _allowed(READ, [role], target_project=OTHER_PROJECT)


def test_support_read_obeys_remote_support_switch():
    assert _allowed(READ, ["SUPPORT"])
    assert not _allowed(READ, ["SUPPORT"], remote_support_enabled=False)


def test_no_membership_does_not_grant_read_access():
    assert not _allowed(READ, [])
