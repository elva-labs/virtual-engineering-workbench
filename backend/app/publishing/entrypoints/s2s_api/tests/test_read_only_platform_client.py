"""A configured read-only platform client (app.shared.middleware.read_only_clients) on the Publishing S2S API."""

import json

import pytest

from app.publishing.entrypoints.s2s_api.tests.test_products import BASE, KEY, READ, WRITE, _dependencies, _invoke
from app.shared.middleware import read_only_clients

PLAN_CLIENT = "plan-client"
PROJECT = BASE.split("/")[2]


@pytest.fixture
def plan_client_configured(monkeypatch):
    monkeypatch.setenv(read_only_clients.ENVIRONMENT_VARIABLE, PLAN_CLIENT)


def test_the_read_only_client_reads_without_an_assignment(plan_client_configured, client_event, lambda_context):
    dependencies = _dependencies()
    product_id = dependencies.products_query_service.add().productId
    event = client_event("GET", f"{BASE}/{product_id}", client_id=PLAN_CLIENT, scopes=[READ])
    response = _invoke(dependencies, event, lambda_context)
    assert response["statusCode"] == 200
    dependencies.project_access_service.require_access.assert_not_called()


def test_the_read_only_client_still_needs_the_read_scope(plan_client_configured, client_event, lambda_context):
    dependencies = _dependencies()
    product_id = dependencies.products_query_service.add().productId
    event = client_event(
        "GET", f"{BASE}/{product_id}", client_id=PLAN_CLIENT, scopes=["clients/publishing/version.read"]
    )
    response = _invoke(dependencies, event, lambda_context)
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "INSUFFICIENT_SCOPE"


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", BASE, {"productName": "x", "productType": "WORKBENCH", "technologyId": "tech-1"}),
        ("PUT", BASE + "/{product}", {"productName": "x"}),
        ("DELETE", BASE + "/{product}", None),
    ],
)
def test_the_read_only_client_is_refused_every_write(
    plan_client_configured, client_event, lambda_context, method, path, body
):
    dependencies = _dependencies()
    product_id = dependencies.products_query_service.add().productId
    event = client_event(
        method,
        path.format(product=product_id),
        json.dumps(body) if body else None,
        headers={"Idempotency-Key": KEY},
        client_id=PLAN_CLIENT,
        scopes=[READ, WRITE],
    )
    response = _invoke(dependencies, event, lambda_context)
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "READ_ONLY_CLIENT"
    dependencies.command_bus.handle.assert_not_called()
    dependencies.project_access_service.require_access.assert_not_called()


@pytest.mark.parametrize("configured", [True, False])
def test_other_clients_still_need_their_assignment(monkeypatch, client_event, lambda_context, configured):
    if configured:
        monkeypatch.setenv(read_only_clients.ENVIRONMENT_VARIABLE, PLAN_CLIENT)
    else:
        monkeypatch.delenv(read_only_clients.ENVIRONMENT_VARIABLE, raising=False)
    dependencies = _dependencies()
    product_id = dependencies.products_query_service.add().productId
    response = _invoke(dependencies, client_event("GET", f"{BASE}/{product_id}", scopes=[READ]), lambda_context)
    assert response["statusCode"] == 200
    dependencies.project_access_service.require_access.assert_called_once_with("client-1", PROJECT)
