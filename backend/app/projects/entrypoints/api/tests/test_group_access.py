"""Additions to the Entra group access in the Projects user API: platform-admin groups, projects
hidden without self-enrolment, and server-side role resolution (e.g. launch)."""

import json
import logging
from unittest import mock

import pytest

from app.projects.domain.model import project, project_assignment, project_group_assignment
from app.projects.domain.ports import projects_query_service, user_directory_service
from app.projects.entrypoints.api import bootstrapper, group_access
from app.projects.entrypoints.api.tests import fake_classes
from app.shared.adapters.message_bus import in_memory_command_bus

G_USERS = "11111111-1111-1111-1111-111111111111"
G_PLATFORM = "6ef5c9f6-de67-4103-8695-6e70d688794a"
NOW = "2026-09-30T00:00:00+00:00"


def _project(project_id):
    return project.Project(projectId=project_id, projectName=project_id, isActive=True)


def _grant(project_id, role, group_id=G_USERS):
    return project_group_assignment.ProjectGroupAssignment(
        projectId=project_id, groupId=group_id, roles=[role], version=1, createDate=NOW, lastUpdateDate=NOW
    )


def _assignment(project_id, roles):
    return project_assignment.Assignment(userId="USER123", projectId=project_id, roles=roles)


# effective_assignment (server-side role resolution, e.g. launch)


def test_effective_assignment_grants_group_roles_without_a_direct_assignment():
    result = group_access.effective_assignment("USER123", "proj-1", None, [_grant("proj-1", "PLATFORM_USER")], False)

    assert result.roles == [project_assignment.Role.PLATFORM_USER]


def test_effective_assignment_adds_group_roles_to_the_direct_ones():
    result = group_access.effective_assignment(
        "USER123", "proj-1", _assignment("proj-1", ["PLATFORM_USER"]), [_grant("proj-1", "PROGRAM_OWNER")], False
    )

    assert sorted(result.roles) == [project_assignment.Role.PLATFORM_USER, project_assignment.Role.PROGRAM_OWNER]


def test_effective_assignment_ignores_grants_on_other_projects():
    assert group_access.effective_assignment("USER123", "proj-1", None, [_grant("proj-2", "ADMIN")], False) is None


def test_effective_assignment_makes_platform_admins_admin():
    result = group_access.effective_assignment("USER123", "proj-any", None, [], True)

    assert result.roles == [project_assignment.Role.ADMIN]


def test_groups_from_claim_reads_cognitos_format_and_drops_non_guids():
    assert group_access.groups_from_claim(f"[{G_USERS},not-a-group]") == []
    assert group_access.groups_from_claim(f"[{G_USERS.upper()}]") == [G_USERS]
    assert group_access.groups_from_claim(None) == []


# Handler


@pytest.fixture
def query_service():
    qs = mock.create_autospec(projects_query_service.ProjectsQueryService, instance=True)
    qs.list_projects_by_user.return_value = ([], None, [])
    qs.list_projects.return_value = ([_project("proj-bound"), _project("proj-x")], None, [])
    qs.get_group_assignments.side_effect = lambda groups: (
        [_grant("proj-bound", "PLATFORM_USER")] if G_USERS in groups else []
    )
    qs.get_project_by_id.return_value = _project("proj-bound")
    return qs


@pytest.fixture
def handler(query_service):
    from app.projects.entrypoints.api import handler

    handler.dependencies = bootstrapper.Dependencies(
        projects_query_service=query_service,
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(),
        technologies_query_service=fake_classes.FakeTechnologiesQueryService(),
        command_bus=in_memory_command_bus.InMemoryCommandBus(logger=mock.create_autospec(spec=logging.Logger)),
        user_directory_service=mock.create_autospec(user_directory_service.UserDirectoryService, instance=True),
    )
    with mock.patch.object(handler, "SELF_ENROLMENT_ENABLED", False), mock.patch.object(
        handler, "PLATFORM_ADMIN_GROUPS", {G_PLATFORM}
    ):
        yield handler


def _with_groups(event, groups):
    event["requestContext"]["authorizer"]["userGroups"] = json.dumps(sorted(groups))
    return event


def test_get_projects_without_self_enrolment_shows_only_granted_programs(handler, authenticated_event, lambda_context):
    event = _with_groups(authenticated_event(None, "/projects", "GET", {"pageSize": "10"}), {G_USERS})

    body = json.loads(handler.handler(event, lambda_context)["body"])

    assert [p["projectId"] for p in body["projects"]] == ["proj-bound"]
    assert body["effectiveAccess"] == [{"projectId": "proj-bound", "roles": ["PLATFORM_USER"]}]


def test_get_projects_without_grants_shows_nothing(handler, authenticated_event, lambda_context):
    result = handler.handler(authenticated_event(None, "/projects", "GET", {"pageSize": "10"}), lambda_context)

    assert json.loads(result["body"])["projects"] == []


def test_platform_admins_see_every_program_as_admin(handler, authenticated_event, lambda_context):
    event = _with_groups(authenticated_event(None, "/projects", "GET", {"pageSize": "10"}), {G_PLATFORM})

    body = json.loads(handler.handler(event, lambda_context)["body"])

    assert [p["projectId"] for p in body["projects"]] == ["proj-bound", "proj-x"]
    assert all(a["roles"] == ["ADMIN"] for a in body["effectiveAccess"])


def test_get_project_groups_lists_the_platform_admin_groups(
    handler, authenticated_event, lambda_context, query_service
):
    query_service.list_project_group_assignments.return_value = [_grant("proj-bound", "PLATFORM_USER")]

    body = json.loads(
        handler.handler(authenticated_event(None, "/projects/proj-bound/groups", "GET"), lambda_context)["body"]
    )

    assert [(a["groupId"], a["roles"]) for a in body["assignments"]] == [(G_USERS, ["PLATFORM_USER"])]
    assert body["platformAdminGroups"] == [G_PLATFORM]


def test_internal_user_assignment_includes_group_roles(lambda_context, authenticated_event):
    # A user reached only through a group grant: launch reads this route .
    from app.projects.entrypoints.api import handler
    from app.projects.entrypoints.api.model import api_model

    queries = mock.create_autospec(spec=projects_query_service.ProjectsQueryService, instance=True)
    queries.get_user_assignment.return_value = None
    queries.get_group_assignments.return_value = [_grant("proj-bound", "PLATFORM_USER")]
    directory = mock.create_autospec(spec=user_directory_service.UserDirectoryService, instance=True)
    directory.get_user_groups_claim.return_value = f"[{G_USERS}]"
    directory.get_user_profile.return_value = user_directory_service.UserProfile(
        email="u@example.com", display_name="U"
    )
    handler.dependencies = bootstrapper.Dependencies(
        projects_query_service=queries,
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(),
        technologies_query_service=fake_classes.FakeTechnologiesQueryService(),
        command_bus=in_memory_command_bus.InMemoryCommandBus(logger=mock.create_autospec(spec=logging.Logger)),
        user_directory_service=directory,
    )

    result = handler.handler(
        authenticated_event(None, "/internal/projects/proj-bound/users/user123", "GET"), lambda_context
    )

    assert result["statusCode"] == 200
    assignment = api_model.GetProjectAssignmentResponse.model_validate_json(result["body"]).assignment
    assert assignment.userId == "USER123"
    assert assignment.roles == ["PLATFORM_USER"]
    # A group-only user has no record: email and name come from the identity provider (launch tags).
    assert (assignment.userEmail, assignment.userDisplayName) == ("u@example.com", "U")
    directory.get_user_groups_claim.assert_called_once_with("USER123")
    queries.get_group_assignments.assert_called_once_with([G_USERS])
