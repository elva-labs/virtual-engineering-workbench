import importlib
import json
from datetime import datetime, timezone
from unittest import mock
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.projects.domain.command_handlers.technologies import delete_technology_command_handler
from app.projects.domain.commands.technologies import update_technology_command
from app.projects.domain.model import project_account, service_client_assignment, technology
from app.projects.domain.ports import projects_query_service
from app.projects.domain.value_objects import account_type_value_object
from app.projects.entrypoints.s2s_api import bootstrapper, idempotency, s2s_exception
from app.projects.entrypoints.s2s_api.model import api_model
from app.projects.entrypoints.s2s_api.tests.fake_classes import (
    FakeEnrolmentsQueryService,
    FakeIdempotencyService,
    FakeTechnologiesQueryService,
)
from app.shared.adapters.message_bus.command_bus import CommandBus
from app.shared.domain.ports.idempotency_service import IdempotencyScope, Reservation, ReservationOutcome


def _dependencies():
    projects = mock.create_autospec(projects_query_service.ProjectsQueryService, instance=True)
    projects.get_service_client_assignment.return_value = service_client_assignment.ServiceClientAssignment(
        clientId="fake_client_id",
        projectId="project-1",
        status=service_client_assignment.ServiceClientAssignmentStatus.ACTIVE,
        grantedBy="admin",
        createDate="2024-01-01T00:00:00Z",
        lastUpdateDate="2024-01-01T00:00:00Z",
    )
    return bootstrapper.Dependencies(
        command_bus=mock.create_autospec(CommandBus, instance=True),
        projects_query_service=projects,
        technologies_query_service=FakeTechnologiesQueryService(),
        enrolment_query_service=FakeEnrolmentsQueryService(),
        idempotency_service=FakeIdempotencyService(),
    )


def _event(authenticated_event, body, path, method, scope, *, key=None):
    event = authenticated_event(body, path, method)
    event["requestContext"]["authorizer"]["claims"]["scope"] = scope
    if key:
        event["headers"]["Idempotency-Key"] = key
    return event


def _invoke(dependencies, event, context):
    with patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        return handler.handler(event, context)


def _body(response):
    return json.loads(response["body"])


def test_exact_technology_read_and_update(lambda_context, authenticated_event):
    dependencies = _dependencies()
    read = _invoke(
        dependencies,
        _event(
            authenticated_event,
            None,
            "/projects/project-1/technologies/3",
            "GET",
            "clients/projects/technology.read",
        ),
        lambda_context,
    )

    assert read["statusCode"] == 200
    assert _body(read)["technologyId"] == "3"
    assert _body(read)["projectId"] == "project-1"

    request = api_model.UpdateTechnologyRequest(name="Terraform", description="IaC")
    updated = _invoke(
        dependencies,
        _event(
            authenticated_event,
            request.model_dump_json(),
            "/projects/project-1/technologies/3",
            "PUT",
            "clients/projects/technology.write",
        ),
        lambda_context,
    )

    assert updated["statusCode"] == 200
    command = dependencies.command_bus.handle.call_args.args[0]
    assert isinstance(command, update_technology_command.UpdateTechnologyCommand)
    assert command.id.value == "3"
    assert command.project_id.value == "project-1"
    assert command.name == "Terraform"
    assert command.description == "IaC"


def test_cross_project_technology_is_not_readable(lambda_context, authenticated_event):
    dependencies = _dependencies()
    dependencies.technologies_query_service.get_technology_by_id = mock.Mock(
        return_value=technology.Technology(id="secret", project_id="other-project", name="Secret")
    )
    response = _invoke(
        dependencies,
        _event(
            authenticated_event,
            None,
            "/projects/project-1/technologies/secret",
            "GET",
            "clients/projects/technology.read",
        ),
        lambda_context,
    )

    assert response["statusCode"] == 404
    assert _body(response)["code"] == "NOT_FOUND"
    assert "Secret" not in response["body"]


def test_missing_scope_and_project_assignment_stop_before_resource_lookup(lambda_context, authenticated_event):
    dependencies = _dependencies()
    dependencies.technologies_query_service.list_technologies = mock.Mock()
    missing_scope = _invoke(
        dependencies,
        _event(
            authenticated_event,
            None,
            "/projects/project-1/technologies",
            "GET",
            "clients/projects/technology.write",
        ),
        lambda_context,
    )
    assert missing_scope["statusCode"] == 403
    assert _body(missing_scope)["code"] == "INSUFFICIENT_SCOPE"
    dependencies.technologies_query_service.list_technologies.assert_not_called()

    dependencies.projects_query_service.get_service_client_assignment.return_value = None
    denied = _invoke(
        dependencies,
        _event(
            authenticated_event,
            None,
            "/projects/project-1/technologies",
            "GET",
            "clients/projects/technology.read",
        ),
        lambda_context,
    )
    assert denied["statusCode"] == 403
    assert _body(denied)["code"] == "PROJECT_ACCESS_DENIED"
    dependencies.technologies_query_service.list_technologies.assert_not_called()


def test_changed_body_with_same_idempotency_key_conflicts(lambda_context, authenticated_event):
    dependencies = _dependencies()
    key = "67b7d6e4-98ba-4f9f-977c-f759f58a7e74"
    first = _invoke(
        dependencies,
        _event(
            authenticated_event,
            api_model.CreateTechnologyRequest(name="Terraform").model_dump_json(),
            "/projects/project-1/technologies",
            "POST",
            "clients/projects/technology.write",
            key=key,
        ),
        lambda_context,
    )
    changed = _invoke(
        dependencies,
        _event(
            authenticated_event,
            api_model.CreateTechnologyRequest(name="OpenTofu").model_dump_json(),
            "/projects/project-1/technologies",
            "POST",
            "clients/projects/technology.write",
            key=key,
        ),
        lambda_context,
    )

    assert first["statusCode"] == 201
    assert changed["statusCode"] == 409
    assert _body(changed)["code"] == "IDEMPOTENCY_KEY_REUSED"
    dependencies.command_bus.handle.assert_called_once()


def test_in_progress_create_returns_conflict(lambda_context, authenticated_event):
    dependencies = _dependencies()
    key = "67b7d6e4-98ba-4f9f-977c-f759f58a7e74"
    request = api_model.CreateTechnologyRequest(name="Terraform")
    scope = IdempotencyScope(
        "fake_client_id",
        "project-1",
        "CREATE_TECHNOLOGY",
        None,
        UUID(key),
    )
    dependencies.idempotency_service.records[scope] = {
        "hash": idempotency.canonical_request_hash(request),
        "resource_id": "tech-pending",
        "status": "IN_PROGRESS",
    }
    response = _invoke(
        dependencies,
        _event(
            authenticated_event,
            request.model_dump_json(),
            "/projects/project-1/technologies",
            "POST",
            "clients/projects/technology.write",
            key=key,
        ),
        lambda_context,
    )

    assert response["statusCode"] == 409
    assert _body(response)["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    assert response.get("multiValueHeaders", {}).get("Retry-After") == ["5"]
    dependencies.command_bus.handle.assert_not_called()


def test_recovery_returns_existing_reserved_technology(lambda_context, authenticated_event):
    dependencies = _dependencies()
    key = "67b7d6e4-98ba-4f9f-977c-f759f58a7e74"
    recovered_id = "tech-recover"
    dependencies.idempotency_service = mock.Mock()
    dependencies.idempotency_service.reserve.return_value = Reservation(ReservationOutcome.RECOVER, recovered_id)
    dependencies.technologies_query_service.get_technology_by_id = mock.Mock(
        return_value=technology.Technology(id=recovered_id, project_id="project-1", name="Terraform")
    )
    response = _invoke(
        dependencies,
        _event(
            authenticated_event,
            api_model.CreateTechnologyRequest(name="Terraform").model_dump_json(),
            "/projects/project-1/technologies",
            "POST",
            "clients/projects/technology.write",
            key=key,
        ),
        lambda_context,
    )

    assert response["statusCode"] == 201
    assert _body(response) == {"technologyId": recovered_id}
    dependencies.command_bus.handle.assert_not_called()
    dependencies.idempotency_service.complete.assert_called_once()


def test_delete_referenced_inactive_account_returns_sanitized_conflict(lambda_context, authenticated_event):
    dependencies = _dependencies()
    inactive_account = project_account.ProjectAccount(
        projectId="project-1",
        awsAccountId="123456789012",
        accountType=account_type_value_object.AccountTypeEnum.USER,
        stage=project_account.ProjectAccountStageEnum.DEV,
        accountStatus=project_account.ProjectAccountStatusEnum.Inactive,
        technologyId="3",
    )
    dependencies.projects_query_service.list_project_accounts.return_value = [inactive_account]
    dependencies.command_bus.handle.side_effect = (
        lambda command: delete_technology_command_handler.handle_delete_technology_command(
            cmd=command,
            uow=mock.Mock(),
            projects_qry_srv=dependencies.projects_query_service,
            msg_bus=mock.Mock(),
        )
    )
    response = _invoke(
        dependencies,
        _event(
            authenticated_event,
            None,
            "/projects/project-1/technologies/3",
            "DELETE",
            "clients/projects/technology.write",
        ),
        lambda_context,
    )

    assert response["statusCode"] == 409
    assert _body(response)["code"] == "TECHNOLOGY_IN_USE"
    assert _body(response)["retryable"] is False
    assert "123456789012" not in response["body"]


@pytest.mark.parametrize(
    "request_model",
    [api_model.CreateTechnologyRequest, api_model.UpdateTechnologyRequest],
)
@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_technology_request_name_must_not_be_blank(request_model, name):
    with pytest.raises(ValidationError):
        request_model(name=name)


def test_blank_create_name_is_rejected_before_reservation_or_dispatch(lambda_context, authenticated_event):
    dependencies = _dependencies()
    response = _invoke(
        dependencies,
        _event(
            authenticated_event,
            json.dumps({"name": "  \t "}),
            "/projects/project-1/technologies",
            "POST",
            "clients/projects/technology.write",
            key="67b7d6e4-98ba-4f9f-977c-f759f58a7e74",
        ),
        lambda_context,
    )

    assert response["statusCode"] == 400
    assert _body(response)["code"] == "INVALID_REQUEST"
    assert dependencies.idempotency_service.records == {}
    dependencies.command_bus.handle.assert_not_called()


def test_nonretryable_s2s_failure_is_completed_and_replayed():
    service = FakeIdempotencyService()
    scope = IdempotencyScope(
        "client",
        "project-1",
        "CREATE_TECHNOLOGY",
        None,
        UUID("67b7d6e4-98ba-4f9f-977c-f759f58a7e74"),
    )
    request = api_model.CreateTechnologyRequest(name="Terraform")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    create = mock.Mock(side_effect=s2s_exception.ResourceConflict())

    with pytest.raises(s2s_exception.ReplayedCreateFailure) as first_error:
        idempotency.execute_create(
            service=service,
            scope=scope,
            request=request,
            resource_id="tech-new",
            resource_exists=lambda _: False,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(201, {"technologyId": resource_id}),
            create=create,
            now=now,
        )

    record = service.records[scope]
    assert first_error.value.status_code == 409
    assert first_error.value.code == "RESOURCE_CONFLICT"
    assert record["status"] == "COMPLETED"
    assert record["body"]["retryable"] is False

    create.reset_mock()
    with pytest.raises(s2s_exception.ReplayedCreateFailure) as replay_error:
        idempotency.execute_create(
            service=service,
            scope=scope,
            request=request,
            resource_id="unused-on-replay",
            resource_exists=lambda _: False,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(201, {"technologyId": resource_id}),
            create=create,
            now=now,
        )

    assert replay_error.value.code == "RESOURCE_CONFLICT"
    create.assert_not_called()


def test_retryable_s2s_failure_leaves_reservation_recoverable():
    service = FakeIdempotencyService()
    scope = IdempotencyScope(
        "client",
        "project-1",
        "CREATE_TECHNOLOGY",
        None,
        UUID("67b7d6e4-98ba-4f9f-977c-f759f58a7e74"),
    )
    request = api_model.CreateTechnologyRequest(name="Terraform")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(s2s_exception.ProjectAccessUnavailable):
        idempotency.execute_create(
            service=service,
            scope=scope,
            request=request,
            resource_id="tech-new",
            resource_exists=lambda _: False,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(201, {"technologyId": resource_id}),
            create=mock.Mock(side_effect=s2s_exception.ProjectAccessUnavailable()),
            now=now,
        )

    # The key is released, not blocked until the lease ends: the retry recovers and creates.
    assert service.records[scope]["status"] == "RELEASED"
    result = idempotency.execute_create(
        service=service,
        scope=scope,
        request=request,
        resource_id="tech-other",
        resource_exists=lambda _: False,
        response_for_id=lambda resource_id: idempotency.StoredCreateResponse(201, {"technologyId": resource_id}),
        create=lambda resource_id: idempotency.StoredCreateResponse(201, {"technologyId": resource_id}),
        now=now,
    )
    assert result.body == {"technologyId": "tech-new"}
    assert service.records[scope]["status"] == "COMPLETED"


def test_unexpected_failure_releases_the_reservation():
    # A 500 (here: the handler raising) must not leave the key IN_PROGRESS for the lease.
    service = FakeIdempotencyService()
    scope = IdempotencyScope(
        "client", "project-1", "CREATE_ACCOUNT", None, UUID("77b7d6e4-98ba-4f9f-977c-f759f58a7e74")
    )
    request = api_model.CreateTechnologyRequest(name="Terraform")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(RuntimeError):
        idempotency.execute_create(
            service=service,
            scope=scope,
            request=request,
            resource_id="acct-new",
            resource_exists=lambda _: False,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(202, {"accountId": resource_id}),
            create=mock.Mock(side_effect=RuntimeError("Repository is not registered with the unit of work.")),
            now=now,
        )

    assert service.records[scope]["status"] == "RELEASED"
