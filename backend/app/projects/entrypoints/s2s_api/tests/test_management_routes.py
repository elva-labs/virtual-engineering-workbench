import importlib
import json
from unittest import mock
from uuid import uuid4

from app.projects.domain.model import (
    project,
    project_assignment,
    project_group_assignment,
    service_client_assignment,
)
from app.projects.domain.ports.projects_query_service import ProjectsQueryService
from app.projects.domain.project_group_assignment_service import (
    ProjectGroupAssignmentService,
)
from app.projects.domain.project_lifecycle_service import ProjectLifecycleService
from app.projects.entrypoints.s2s_api.bootstrapper import Dependencies
from app.projects.entrypoints.s2s_api.tests.fake_classes import (
    FakeEnrolmentsQueryService,
    FakeIdempotencyService,
    FakeTechnologiesQueryService,
)
from app.shared.adapters.message_bus.command_bus import CommandBus

GROUP_ID = "a0242041-5460-497a-a98e-710196dd9e8f"


def make_dependencies():
    query = mock.create_autospec(ProjectsQueryService, instance=True)
    query.get_service_client_assignment.side_effect = lambda project_id, client_id: (
        service_client_assignment.ServiceClientAssignment(
            clientId=client_id,
            projectId=project_id,
            status="ACTIVE",
            grantedBy=client_id,
            createDate="2026-09-01",
            lastUpdateDate="2026-09-01",
        )
        if project_id == "project-id" and client_id == "fake_client_id"
        else None
    )
    inactive = project.Project(
        projectId="project-id",
        projectName="Existing",
        projectDescription=None,
        isActive=False,
        createDate="2026-09-01",
        lastUpdateDate="2026-09-02",
    )
    query.get_project_by_id.return_value = inactive
    query.get_user_assignment.return_value = None
    query.get_project_group_assignment.return_value = None
    query.list_project_group_assignments.return_value = []
    lifecycle = mock.create_autospec(ProjectLifecycleService, instance=True)
    lifecycle.create.return_value = "project-id"
    lifecycle.update.return_value = inactive.model_copy(update={"isActive": True})
    lifecycle.deactivate.return_value = inactive
    groups = mock.create_autospec(ProjectGroupAssignmentService, instance=True)
    command_bus = mock.create_autospec(CommandBus, instance=True)
    deps = Dependencies(
        command_bus=command_bus,
        projects_query_service=query,
        technologies_query_service=FakeTechnologiesQueryService(),
        enrolment_query_service=FakeEnrolmentsQueryService(),
        project_lifecycle_service=lifecycle,
        group_assignment_service=groups,
        idempotency_service=FakeIdempotencyService(),
    )
    return deps, query, lifecycle, groups, command_bus


def invoke(
    deps, authenticated_event, lambda_context, method, path, body=None, key=None
):
    with mock.patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap", return_value=deps
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        event = authenticated_event(
            json.dumps(body) if body is not None else None, path, method
        )
        if key:
            event["headers"]["Idempotency-Key"] = key
        return handler.handler(event, lambda_context)


def test_project_create_exact_inactive_update_and_deactivate(
    lambda_context, authenticated_event
):
    deps, query, lifecycle, _, _ = make_dependencies()
    key = str(uuid4())
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "POST",
        "/projects",
        {"name": "New", "description": None, "isActive": True},
        key,
    )
    assert response["statusCode"] == 201
    assert json.loads(response["body"]) == {"projectId": "project-id"}
    lifecycle.create.assert_called_once_with("fake_client_id", key, "New", None, True)
    response = invoke(
        deps, authenticated_event, lambda_context, "GET", "/projects/project-id"
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["isActive"] is False
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        "/projects/project-id",
        {"name": "New", "description": None, "isActive": True},
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["isActive"] is True
    response = invoke(
        deps, authenticated_event, lambda_context, "DELETE", "/projects/project-id"
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["isActive"] is False


def test_group_wire_and_project_isolation(lambda_context, authenticated_event):
    deps, query, _, groups, _ = make_dependencies()
    record = project_group_assignment.ProjectGroupAssignment(
        projectId="project-id",
        groupId=GROUP_ID,
        roles=["PLATFORM_USER"],
        version=1,
        createDate="2026-09-01",
        lastUpdateDate="2026-09-01",
    )
    groups.put.return_value = record
    query.get_project_group_assignment.return_value = record
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        f"/projects/project-id/groups/{GROUP_ID}",
        {"roles": ["PLATFORM_USER"]},
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["groupId"] == GROUP_ID
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "GET",
        f"/projects/project-id/groups/{GROUP_ID}",
    )
    assert response["statusCode"] == 200
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "DELETE",
        f"/projects/project-id/groups/{GROUP_ID}",
    )
    assert response["statusCode"] == 204
    groups.delete.assert_called_once_with("project-id", GROUP_ID)
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        "/projects/project-id/groups/not-a-uuid",
        {"roles": ["ADMIN"]},
    )
    assert response["statusCode"] == 400
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        f"/projects/other-project/groups/{GROUP_ID}",
        {"roles": ["ADMIN"]},
    )
    assert response["statusCode"] == 403
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        "/projects/other-project",
        {"name": "Cross", "description": None, "isActive": True},
    )
    assert response["statusCode"] == 403


def test_direct_user_exact_distinguishes_absent_and_empty_with_metadata(
    lambda_context, authenticated_event
):
    deps, query, _, _, commands = make_dependencies()
    path = "/projects/project-id/users/UNKNOWN-USER"
    response = invoke(deps, authenticated_event, lambda_context, "GET", path)
    assert response["statusCode"] == 404
    query.get_user_assignment.return_value = project_assignment.Assignment(
        projectId="project-id",
        userId="UNKNOWN-USER",
        roles=[],
        userEmail="new@example.com",
        userDisplayName="New User",
    )
    response = invoke(deps, authenticated_event, lambda_context, "GET", path)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "projectId": "project-id",
        "userId": "UNKNOWN-USER",
        "roles": [],
        "userEmail": "new@example.com",
        "userDisplayName": "New User",
    }
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "POST",
        "/projects/project-id/users",
        {
            "userId": "unknown-user",
            "roles": ["PLATFORM_USER"],
            "userEmail": "new@example.com",
            "userDisplayName": "New User",
        },
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["userDisplayName"] == "New User"
    command = commands.handle.call_args.args[0]
    assert (
        command.user_email == "new@example.com"
        and command.user_display_name == "New User"
    )
