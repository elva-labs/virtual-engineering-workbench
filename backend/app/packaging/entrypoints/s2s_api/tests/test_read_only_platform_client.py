"""A configured read-only platform client (app.shared.middleware.read_only_clients) on the Packaging S2S API."""

import json

import pytest

from app.packaging.domain.exceptions.s2s_exception import ProjectAccessDenied
from app.packaging.entrypoints.s2s_api.tests.test_handler import load_handler
from app.shared.middleware import read_only_clients

PLAN_CLIENT = "plan-client"
READ = "clients/packaging/component.read"
WRITE = "clients/packaging/component.write"
KEY = "8a4b6f0e-0a0b-4c1d-9e2f-3a4b5c6d7e8f"


@pytest.fixture
def plan_client_configured(monkeypatch):
    monkeypatch.setenv(read_only_clients.ENVIRONMENT_VARIABLE, PLAN_CLIENT)


def test_the_read_only_client_reads_without_an_assignment(
    plan_client_configured, monkeypatch, mocked_dependencies, lambda_context, client_event
):
    # Other clients are refused here: the plan client must not even ask.
    mocked_dependencies.project_access_service.require_access.side_effect = ProjectAccessDenied()
    mocked_dependencies.component_domain_qry_srv.get_components.return_value = []
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event("GET", "/projects/proj-1/components", client_id=PLAN_CLIENT, scopes=[READ]), lambda_context
    )
    assert response["statusCode"] == 200, response["body"]
    mocked_dependencies.project_access_service.require_access.assert_not_called()


def test_the_read_only_client_still_needs_the_read_scope(
    plan_client_configured, monkeypatch, mocked_dependencies, lambda_context, client_event
):
    event = client_event(
        "GET", "/projects/proj-1/components", client_id=PLAN_CLIENT, scopes=["clients/packaging/recipe.read"]
    )
    response = load_handler(monkeypatch, mocked_dependencies).handler(event, lambda_context)
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "INSUFFICIENT_SCOPE"


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/projects/proj-1/components", "component"),
        ("PUT", "/projects/proj-1/components/comp-1", {"componentDescription": "x"}),
        ("DELETE", "/projects/proj-1/components/comp-1", None),
        ("PUT", "/base-images/arm64/prod", {"projectId": "proj-1", "imageId": "image-1"}),
    ],
)
def test_the_read_only_client_is_refused_every_write(
    plan_client_configured,
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    component_body,
    method,
    path,
    body,
):
    event = client_event(
        method,
        path,
        body=component_body if body == "component" else body,
        headers={"Idempotency-Key": KEY},
        client_id=PLAN_CLIENT,
        scopes=[READ, WRITE, "clients/packaging/component.release"],
    )
    response = load_handler(monkeypatch, mocked_dependencies).handler(event, lambda_context)
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "READ_ONLY_CLIENT"
    mocked_dependencies.command_bus.handle.assert_not_called()
    mocked_dependencies.project_access_service.require_access.assert_not_called()


@pytest.mark.parametrize("configured", [True, False])
def test_other_clients_still_need_their_assignment(
    monkeypatch, mocked_dependencies, lambda_context, client_event, configured
):
    if configured:
        monkeypatch.setenv(read_only_clients.ENVIRONMENT_VARIABLE, PLAN_CLIENT)
    else:
        monkeypatch.delenv(read_only_clients.ENVIRONMENT_VARIABLE, raising=False)
    mocked_dependencies.project_access_service.require_access.side_effect = ProjectAccessDenied()
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event("GET", "/projects/proj-1/components", scopes=[READ]), lambda_context
    )
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "PROJECT_ACCESS_DENIED"
    mocked_dependencies.project_access_service.require_access.assert_called_once_with("client-1", "proj-1")
