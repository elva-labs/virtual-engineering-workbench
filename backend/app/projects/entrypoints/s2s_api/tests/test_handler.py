import importlib
import json
import logging
import unittest
from datetime import datetime, timezone
from unittest import mock
from unittest.mock import create_autospec, patch

import assertpy

from app.projects.domain.command_handlers.enrolments import (
    approve_enrolments_command_handler,
    enrol_user_to_program_command_handler,
)
from app.projects.domain.commands.enrolments import approve_enrolments_command, enrol_user_to_program_command
from app.projects.domain.commands.service_clients import (
    put_service_client_assignment_command,
    revoke_service_client_assignment_command,
)
from app.projects.domain.model import enrolment, project_assignment, service_client_assignment
from app.projects.domain.ports import projects_query_service
from app.projects.entrypoints.s2s_api import bootstrapper, common
from app.projects.entrypoints.s2s_api.model import api_model
from app.projects.entrypoints.s2s_api.tests import fake_classes
from app.shared.adapters.idempotency.dynamodb_idempotency_service import DynamoDBIdempotencyService
from app.shared.adapters.message_bus import command_bus, event_bridge_message_bus, in_memory_command_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work as shared_dynamodb_unit_of_work
from app.shared.domain.ports.idempotency_service import IdempotencyScope


def get_mock_dependencies(
    projects_query_service=None,
    enrolment_query_service=None,
) -> bootstrapper.Dependencies:
    command_bus = (
        in_memory_command_bus.InMemoryCommandBus(logger=create_autospec(spec=logging.Logger))
        .register_handler(
            approve_enrolments_command.ApproveEnrolmentsCommand,
            lambda command: approve_enrolments_command_handler.handle_approve_enrolments_command(
                cmd=command,
                uow=unittest.mock.create_autospec(
                    spec=shared_dynamodb_unit_of_work.UnitOfWork,
                ),
                enrolment_qry_srv=enrolment_query_service or fake_classes.FakeEnrolmentsQueryService(),
                project_qry_srv=projects_query_service or fake_classes.FakeProjectsQueryService(),
                message_bus=unittest.mock.create_autospec(
                    spec=event_bridge_message_bus.EventBridgeMessageBus,
                ),
            ),
        )
        .register_handler(
            enrol_user_to_program_command.EnrolUserToProgramCommand,
            lambda command: enrol_user_to_program_command_handler.handle_enrol_user_to_program_command(
                cmd=command,
                uow=unittest.mock.create_autospec(
                    spec=shared_dynamodb_unit_of_work.UnitOfWork,
                ),
                projects_qry_srv=projects_query_service or fake_classes.FakeProjectsQueryService(),
                enrolment_qry_srv=enrolment_query_service or fake_classes.FakeEnrolmentsQueryService(),
                msg_bus=unittest.mock.create_autospec(
                    spec=event_bridge_message_bus.EventBridgeMessageBus,
                ),
            ),
        )
    )

    return bootstrapper.Dependencies(
        technologies_query_service=fake_classes.FakeTechnologiesQueryService(),
        projects_query_service=projects_query_service or fake_classes.FakeProjectsQueryService(),
        enrolment_query_service=enrolment_query_service or fake_classes.FakeEnrolmentsQueryService(),
        command_bus=command_bus,
        idempotency_service=fake_classes.FakeIdempotencyService(),
    )


@patch(
    "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
    return_value=get_mock_dependencies(),
)
def test_get_projects(lambda_context, authenticated_event):
    # Arrange
    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    minimal_event = authenticated_event(None, "/projects", "GET", {"pageSize": "25"})

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)
    response = api_model.GetProjectsResponse.model_validate(json.loads(result["body"]))
    assertpy.assert_that(response).is_not_none()
    assertpy.assert_that(len(response.projects)).is_equal_to(5)


@patch("app.projects.entrypoints.s2s_api.routers.enrolments.uuid4", return_value="testUUID")
@patch(
    "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
    return_value=get_mock_dependencies(
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(
            enrolments=[enrolment.Enrolment(id="testUUID", projectId="P0", userId="user_id", status="Pending")]
        )
    ),
)
def test_can_approve_enrolment_for_new_user(mock_deps, mock_uuid, lambda_context, authenticated_event):
    # Arrange
    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    project_id = "P0"
    user_id = "user"
    request = api_model.EnrolUserRequest(
        userId=user_id, userEmail="test@test.de", approverId="testApprover"
    ).model_dump_json()
    minimal_event = authenticated_event(request, f"/projects/{project_id}/enrolments", "POST")

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)


@patch("app.projects.entrypoints.s2s_api.routers.enrolments.uuid4", return_value="testUUID")
@patch(
    "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
    return_value=get_mock_dependencies(
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(
            enrolments=[
                enrolment.Enrolment(
                    id="testUUID",
                    projectId="P0",
                    userId="userWithPendingEnrolment",
                    status="Pending",
                )
            ]
        )
    ),
)
def test_can_approve_enrolment_for_already_enrolled_but_pending_user(
    mock_deps, mock_uuid, lambda_context, authenticated_event
):
    # Arrange
    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    project_id = "P0"
    user_id = "userWithPendingEnrolment"
    request = api_model.EnrolUserRequest(
        userId=user_id, userEmail="test@test.de", approverId="testApprover"
    ).model_dump_json()
    minimal_event = authenticated_event(request, f"/projects/{project_id}/enrolments", "POST")

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)


@patch(
    "app.projects.adapters.query_services.dynamodb_query_service.DynamoDBProjectsQueryService",
    autospec=True,
)
def test_get_project_users_should_return_all_users(projects_qs_mock, lambda_context, authenticated_event):
    # Arrange
    projects_qs_mock.return_value = fake_classes.FakeProjectsQueryService()

    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    minimal_event = authenticated_event(None, "/projects/project-id/users", "GET")

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)
    assertpy.assert_that(json.loads(result["body"])).is_equal_to(
        {
            "assignments": [
                {
                    "projectId": "project-id",
                    "userDisplayName": None,
                    "userId": "T0000AA",
                    "roles": [project_assignment.Role.PLATFORM_USER.value],
                    "userEmail": None,
                }
            ],
        }
    )


@patch(
    "app.projects.domain.command_handlers.users.assign_user_command_handler.handle_assign_user_command",
    autospec=True,
)
def test_assign_user_should_create_assignment(mock_command_handler, lambda_context, authenticated_event):
    # Arrange
    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    request = api_model.AssignUserRequest(userId="T0000AA", roles=[project_assignment.Role.PLATFORM_USER.value])
    minimal_event = authenticated_event(request.model_dump_json(), "/projects/project-id/users", "POST")

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)
    mock_command_handler.assert_called_once()


@patch(
    "app.projects.domain.command_handlers.users.reassign_user_command_handler.handle_reassign_user_command",
    autospec=True,
)
def test_reassign_user_should_update_assignment(mock_command_handler, lambda_context, authenticated_event):
    # Arrange
    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    request = api_model.ReAssignUsersRequest.model_validate({"roles": [project_assignment.Role.PLATFORM_USER.value]})
    minimal_event = authenticated_event(json.dumps(request.model_dump()), "/projects/project-id/users", "PUT")

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)
    mock_command_handler.assert_called_once()


@patch(
    "app.projects.adapters.query_services.dynamodb_query_service.DynamoDBProjectsQueryService",
    autospec=True,
)
def test_get_user_roles(projects_qs_mock, lambda_context, authenticated_event):
    # Arrange
    projects_qs_mock.return_value = fake_classes.FakeProjectsQueryService()

    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    minimal_event = authenticated_event(None, "/projects/123/users/U0", "GET")

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)
    response = api_model.GetUserRolesResponse.model_validate(json.loads(result["body"]))
    assertpy.assert_that(response.roles).is_equal_to([project_assignment.Role.ADMIN.value, project_assignment.Role.PLATFORM_USER.value])
    assert response.userId == "U0"
    assert response.projectId == "123"


@patch(
    "app.projects.domain.command_handlers.users.unassign_user_command_handler.handle_unassign_user_command",
    autospec=True,
)
def test_offboard_multiple_users_should_remove_assignments(mock_command_handler, lambda_context, authenticated_event):
    # Arrange
    from app.projects.entrypoints.s2s_api import handler

    importlib.reload(handler)

    user_id = "T0000AA"
    request = api_model.RemoveUsersRequest(userIds=[user_id]).model_dump_json()
    minimal_event = authenticated_event(request, "/projects/project-id/users", "DELETE")

    # Act
    result = handler.handler(minimal_event, lambda_context)

    # Assert
    assertpy.assert_that(result["statusCode"]).is_equal_to(200)
    mock_command_handler.assert_called_once()


def service_client_dependencies(assignment=None):
    projects_query_service_mock = mock.create_autospec(
        projects_query_service.ProjectsQueryService,
        instance=True,
    )
    caller = service_client_assignment.ServiceClientAssignment(
        clientId="fake_client_id", projectId="proj-1", status="ACTIVE", grantedBy="fake_client_id",
        createDate="2026-09-01", lastUpdateDate="2026-09-01",
    )
    projects_query_service_mock.get_service_client_assignment.side_effect = lambda project_id, client_id: (caller if client_id == "fake_client_id" else assignment)
    command_bus_mock = mock.create_autospec(command_bus.CommandBus, instance=True)
    dependencies = bootstrapper.Dependencies(
        technologies_query_service=fake_classes.FakeTechnologiesQueryService(),
        projects_query_service=projects_query_service_mock,
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(),
        command_bus=command_bus_mock,
        idempotency_service=fake_classes.FakeIdempotencyService(),
    )
    return dependencies, command_bus_mock


def test_put_service_client_assignment_dispatches_command(lambda_context, authenticated_event):
    dependencies, command_bus_mock = service_client_dependencies()
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        response = handler.handler(
            authenticated_event(None, "/projects/proj-1/clients/client-1", "PUT"),
            lambda_context,
        )

    assert response["statusCode"] == 200
    dispatched = command_bus_mock.handle.call_args.args[0]
    assert isinstance(
        dispatched,
        put_service_client_assignment_command.PutServiceClientAssignmentCommand,
    )
    assert dispatched.project_id.value == "proj-1"
    assert dispatched.client_id == "client-1"
    assert dispatched.granted_by == "fake_client_id"


def test_get_service_client_assignment_returns_assignment(lambda_context, authenticated_event):
    assignment = service_client_assignment.ServiceClientAssignment(
        clientId="client-1",
        projectId="proj-1",
        status=service_client_assignment.ServiceClientAssignmentStatus.ACTIVE,
        grantedBy="admin-client",
        createDate="2026-09-16T10:00:00+00:00",
        lastUpdateDate="2026-09-16T10:00:00+00:00",
    )
    dependencies, _ = service_client_dependencies(assignment)
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        response = handler.handler(
            authenticated_event(None, "/projects/proj-1/clients/client-1", "GET"),
            lambda_context,
        )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["assignment"] == {
        "clientId": "client-1",
        "projectId": "proj-1",
        "status": "ACTIVE",
    }


def test_get_service_client_assignment_returns_404_when_missing(lambda_context, authenticated_event):
    dependencies, _ = service_client_dependencies()
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        response = handler.handler(
            authenticated_event(None, "/projects/proj-1/clients/missing", "GET"),
            lambda_context,
        )

    assert response["statusCode"] == 404
    dependencies.projects_query_service.get_service_client_assignment.assert_any_call("proj-1", "missing")


def test_delete_service_client_assignment_dispatches_command(lambda_context, authenticated_event):
    dependencies, command_bus_mock = service_client_dependencies()
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        response = handler.handler(
            authenticated_event(None, "/projects/proj-1/clients/client-1", "DELETE"),
            lambda_context,
        )

    assert response["statusCode"] == 200
    dispatched = command_bus_mock.handle.call_args.args[0]
    assert isinstance(
        dispatched,
        revoke_service_client_assignment_command.RevokeServiceClientAssignmentCommand,
    )
    assert dispatched.project_id.value == "proj-1"
    assert dispatched.client_id == "client-1"
    assert dispatched.revoked_by == "fake_client_id"


def _technology_dependencies():
    project_service = mock.create_autospec(projects_query_service.ProjectsQueryService, instance=True)
    project_service.get_service_client_assignment.return_value = service_client_assignment.ServiceClientAssignment(
        clientId="fake_client_id",
        projectId="project-1",
        status=service_client_assignment.ServiceClientAssignmentStatus.ACTIVE,
        grantedBy="admin",
        createDate="2024-01-01T00:00:00Z",
        lastUpdateDate="2024-01-01T00:00:00Z",
    )
    dependencies = get_mock_dependencies(projects_query_service=project_service)
    dependencies.command_bus = mock.create_autospec(command_bus.CommandBus, instance=True)
    return dependencies


def _set_scope(event, scope):
    event["requestContext"]["authorizer"]["claims"]["scope"] = scope
    return event


def test_list_technologies_checks_assignment_and_exact_read_scope(lambda_context, authenticated_event):
    dependencies = _technology_dependencies()
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        event = _set_scope(
            authenticated_event(None, "/projects/project-1/technologies", "GET"),
            "clients/projects/technology.read",
        )
        response = handler.handler(event, lambda_context)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["technologies"][0]["technologyId"] == "0"
    dependencies.projects_query_service.get_service_client_assignment.assert_called_once_with(
        "project-1", "fake_client_id"
    )


def test_create_technology_replays_same_idempotent_response(lambda_context, authenticated_event):
    dependencies = _technology_dependencies()
    request = api_model.CreateTechnologyRequest(name="Terraform", description="IaC").model_dump_json()
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        event = authenticated_event(request, "/projects/project-1/technologies", "POST")
        event["headers"]["Idempotency-Key"] = "67b7d6e4-98ba-4f9f-977c-f759f58a7e74"
        _set_scope(event, "clients/projects/technology.write")
        first = handler.handler(event, lambda_context)
        second = handler.handler(event, lambda_context)

    assert first["statusCode"] == second["statusCode"] == 201
    assert json.loads(first["body"]) == json.loads(second["body"])
    dependencies.command_bus.handle.assert_called_once()


def test_create_technology_rejects_missing_idempotency_key(lambda_context, authenticated_event):
    dependencies = _technology_dependencies()
    request = api_model.CreateTechnologyRequest(name="Terraform").model_dump_json()
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        event = _set_scope(
            authenticated_event(request, "/projects/project-1/technologies", "POST"),
            "clients/projects/technology.write",
        )
        response = handler.handler(event, lambda_context)

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["code"] == "INVALID_IDEMPOTENCY_KEY"
    assert dependencies.idempotency_service.records == {}


def test_delete_missing_technology_is_idempotent(lambda_context, authenticated_event):
    dependencies = _technology_dependencies()
    dependencies.technologies_query_service.get_technology_by_id = mock.Mock(return_value=None)
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        event = _set_scope(
            authenticated_event(None, "/projects/project-1/technologies/tech-missing", "DELETE"),
            "clients/projects/technology.write",
        )
        response = handler.handler(event, lambda_context)

    assert response["statusCode"] == 204
    dependencies.command_bus.handle.assert_not_called()


def test_unhandled_projects_s2s_error_returns_sanitized_problem_details(lambda_context, authenticated_event):
    dependencies = _technology_dependencies()
    dependencies.technologies_query_service.list_technologies = mock.Mock(
        side_effect=RuntimeError("secret table and request data")
    )
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        event = _set_scope(
            authenticated_event(None, "/projects/project-1/technologies", "GET"),
            "clients/projects/technology.read",
        )
        response = handler.handler(event, lambda_context)

    body = json.loads(response["body"])
    assert response["statusCode"] == 500
    assert body["code"] == "INTERNAL_ERROR"
    assert body["requestId"] == event["requestContext"]["requestId"]
    assert body["retryable"] is True
    assert "secret" not in response["body"]


def test_projects_idempotency_storage_hashes_key_token():
    from uuid import UUID

    client = mock.Mock()
    client.exceptions.ConditionalCheckFailedException = type("ConditionalCheckFailedException", (Exception,), {})
    key = UUID("67b7d6e4-98ba-4f9f-977c-f759f58a7e74")
    service = DynamoDBIdempotencyService("table", client, key_token=common.hash_idempotency_key)
    service.reserve(
        IdempotencyScope("client", "project", "CREATE_TECHNOLOGY", None, key),
        "request-hash",
        "tech-abcde",
        datetime.now(timezone.utc),
    )

    stored_item = client.put_item.call_args.kwargs["Item"]
    assert stored_item["idempotencyKey"] == common.hash_idempotency_key(key)
    assert stored_item["SK"].endswith(common.hash_idempotency_key(key))
    assert str(key) not in str(stored_item)
