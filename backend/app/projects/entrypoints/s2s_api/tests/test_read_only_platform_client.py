"""A configured read-only platform client (app.shared.middleware.read_only_clients) on the Projects S2S API."""

import importlib
import json
from unittest import mock
from uuid import uuid4

import pytest

from app.projects.domain.model import service_client_assignment
from app.projects.entrypoints.s2s_api.tests.test_management_routes import make_dependencies
from app.shared.middleware import read_only_clients

PLAN_CLIENT = "plan-client"
READ_SCOPES = (
    "clients/projects/program.read clients/projects/assignment.read clients/projects/group_assignment.read "
    "clients/projects/client_assignment.read"
)
ALL_SCOPES = READ_SCOPES + (
    " clients/projects/program.write clients/projects/assignment.write clients/projects/group_assignment.write"
    " clients/projects/client_assignment.write clients/projects/client_assignment.bootstrap"
)


@pytest.fixture
def plan_client_configured(monkeypatch):
    monkeypatch.setenv(read_only_clients.ENVIRONMENT_VARIABLE, PLAN_CLIENT)


def invoke(deps, authenticated_event, lambda_context, method, path, client_id, scopes, body=None):
    with mock.patch("app.projects.entrypoints.s2s_api.bootstrapper.bootstrap", return_value=deps):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        event = authenticated_event(json.dumps(body) if body is not None else None, path, method)
        event["requestContext"]["authorizer"]["claims"].update(client_id=client_id, sub=client_id, scope=scopes)
        event["headers"]["Idempotency-Key"] = str(uuid4())
        return handler.handler(event, lambda_context)


def assignment(project_id, client_id):
    return service_client_assignment.ServiceClientAssignment(
        clientId=client_id,
        projectId=project_id,
        status="ACTIVE",
        grantedBy=client_id,
        createDate="2026-09-01",
        lastUpdateDate="2026-09-01",
    )


@pytest.mark.parametrize("project_id", ["project-id", "proj-other", "proj-ydt7d"])
@pytest.mark.parametrize(
    "path, status",
    [
        ("/projects/{p}", 200),
        # Authorized; the program simply has no management mark.
        ("/projects/{p}/management", 404),
        ("/projects/{p}/clients/terraform-client", 200),
    ],
)
def test_the_read_only_client_reads_every_program_without_an_assignment(
    plan_client_configured, authenticated_event, lambda_context, project_id, path, status
):
    deps, query, *_ = make_dependencies()
    # The Terraform client is assigned everywhere; the plan client nowhere.
    query.get_service_client_assignment.side_effect = lambda project, client: (
        assignment(project, client) if client == "terraform-client" else None
    )
    response = invoke(
        deps, authenticated_event, lambda_context, "GET", path.format(p=project_id), PLAN_CLIENT, READ_SCOPES
    )
    assert response["statusCode"] == status, response["body"]
    if status == 404:
        assert json.loads(response["body"])["code"] == "NOT_FOUND"
    # Never by an assignment of its own: the only lookup is the route's resource.
    assert mock.call(project_id, PLAN_CLIENT) not in query.get_service_client_assignment.call_args_list


def test_the_read_only_client_still_needs_the_read_scope(plan_client_configured, authenticated_event, lambda_context):
    deps, *_ = make_dependencies()
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "GET",
        "/projects/proj-other",
        PLAN_CLIENT,
        "clients/projects/group_assignment.read",
    )
    assert response["statusCode"] == 403


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/projects", {"name": "New", "description": None, "isActive": True}),
        ("PUT", "/projects/project-id", {"name": "New", "description": None, "isActive": True}),
        ("DELETE", "/projects/project-id", None),
        ("PUT", "/projects/proj-other/clients/plan-client", None),
        ("DELETE", "/projects/project-id/clients/terraform-client", None),
        ("PUT", "/projects/project-id/management", {"managedBy": "terraform", "source": "repo"}),
        ("DELETE", "/projects/project-id/groups/a0242041-5460-497a-a98e-710196dd9e8f", None),
    ],
)
def test_the_read_only_client_is_refused_every_write_even_with_write_scopes(
    plan_client_configured, authenticated_event, lambda_context, method, path, body
):
    deps, query, lifecycle, groups, command_bus = make_dependencies()
    # Even assigned and holding every scope, it may not write.
    query.get_service_client_assignment.side_effect = lambda project, client: assignment(project, client)
    response = invoke(deps, authenticated_event, lambda_context, method, path, PLAN_CLIENT, ALL_SCOPES, body)
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "READ_ONLY_CLIENT"
    command_bus.handle.assert_not_called()
    lifecycle.create.assert_not_called()
    lifecycle.update.assert_not_called()
    lifecycle.deactivate.assert_not_called()
    query.get_service_client_assignment.assert_not_called()


@pytest.mark.parametrize("configured", [True, False])
def test_other_clients_still_need_an_active_assignment(monkeypatch, authenticated_event, lambda_context, configured):
    if configured:
        monkeypatch.setenv(read_only_clients.ENVIRONMENT_VARIABLE, PLAN_CLIENT)
    else:
        monkeypatch.delenv(read_only_clients.ENVIRONMENT_VARIABLE, raising=False)
    deps, *_ = make_dependencies()
    # fake_client_id is assigned to project-id only.
    for path in ("/projects/proj-other", "/projects/proj-other/management"):
        response = invoke(deps, authenticated_event, lambda_context, "GET", path, "fake_client_id", ALL_SCOPES)
        assert response["statusCode"] == 403
        assert json.loads(response["body"])["code"] == "PROJECT_ACCESS_DENIED"
    response = invoke(
        deps, authenticated_event, lambda_context, "GET", "/projects/project-id", "fake_client_id", ALL_SCOPES
    )
    assert response["statusCode"] == 200
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        "/projects/project-id",
        "fake_client_id",
        ALL_SCOPES,
        {"name": "New", "description": None, "isActive": True},
    )
    assert response["statusCode"] == 200


def test_without_the_setting_the_plan_client_needs_an_assignment(monkeypatch, authenticated_event, lambda_context):
    monkeypatch.delenv(read_only_clients.ENVIRONMENT_VARIABLE, raising=False)
    deps, *_ = make_dependencies()
    response = invoke(
        deps, authenticated_event, lambda_context, "GET", "/projects/proj-other", PLAN_CLIENT, READ_SCOPES
    )
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "PROJECT_ACCESS_DENIED"
