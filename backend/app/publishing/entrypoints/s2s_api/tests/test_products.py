import importlib
import json
from unittest import mock
from unittest.mock import patch

import pytest

from app.publishing.domain.commands import archive_product_command, create_product_command, update_product_command
from app.publishing.domain.exceptions import domain_exception, s2s_exception
from app.publishing.domain.model import product
from app.publishing.domain.ports.service_client_project_access_service import ServiceClientProjectAccessService
from app.publishing.domain.ports.technologies_query_service import TechnologiesQueryService
from app.publishing.domain.read_models import technology
from app.publishing.entrypoints.s2s_api import bootstrapper
from app.shared.adapters.message_bus.command_bus import CommandBus
from app.shared.domain.ports.idempotency_service import IdempotencyService, Reservation, ReservationOutcome

READ = "clients/publishing/product.read"
WRITE = "clients/publishing/product.write"
KEY = "8a4b6f0e-0a0b-4c1d-9e2f-3a4b5c6d7e8f"
BASE = "/projects/proj-12345/products"


class FakeIdempotencyService(IdempotencyService):
    def __init__(self):
        self.records = {}

    def reserve(self, scope, request_hash, resource_id, now):
        existing = self.records.get(scope)
        if existing is None:
            self.records[scope] = {"hash": request_hash, "resource_id": resource_id, "status": "IN_PROGRESS"}
            return Reservation(ReservationOutcome.ACQUIRED, resource_id)
        if existing["hash"] != request_hash:
            return Reservation(ReservationOutcome.CONFLICT, existing["resource_id"])
        if existing["status"] == "COMPLETED":
            return Reservation(
                ReservationOutcome.REPLAY, existing["resource_id"], existing["status_code"], existing["body"]
            )
        return Reservation(ReservationOutcome.IN_PROGRESS, existing["resource_id"])

    def complete(self, scope, request_hash, resource_id, response_status, response_body, now):
        self.records[scope].update(status="COMPLETED", status_code=response_status, body=response_body)


class FakeProducts:
    """In-memory products; the command bus writes through it like the real handlers would."""

    def __init__(self):
        self.items: dict[str, product.Product] = {}

    def get_product(self, project_id, product_id):
        item = self.items.get(product_id)
        return item if item is not None and item.projectId == project_id else None

    def get_products(self, project_id):
        return [item for item in self.items.values() if item.projectId == project_id]

    def add(self, product_id="prod-12345678", status=product.ProductStatus.Created, project_id="proj-12345"):
        self.items[product_id] = product.Product(
            projectId=project_id,
            productId=product_id,
            technologyId="tech-1",
            technologyName="Ubuntu",
            status=status,
            productName="Example workbench arm64",
            productType=product.ProductType.Workbench,
            productDescription="Example product",
            availableStages=[product.ProductStage.DEV],
            createDate="2026-09-29T00:00:00+00:00",
            lastUpdateDate="2026-09-29T00:00:00+00:00",
            createdBy="service:client-1",
            lastUpdatedBy="service:client-1",
        )
        return self.items[product_id]


def _dependencies():
    products = FakeProducts()
    bus = mock.create_autospec(CommandBus, instance=True)

    def handle(command):
        if isinstance(command, create_product_command.CreateProductCommand):
            products.add(command.productId.value)
            products.items[command.productId.value].productName = command.productName.value
        elif isinstance(command, update_product_command.UpdateProductCommand):
            products.items[command.productId.value].productName = command.productName.value
            products.items[command.productId.value].productDescription = command.productDescription.value
        elif isinstance(command, archive_product_command.ArchiveProductCommand):
            products.items[command.productId.value].status = product.ProductStatus.Archiving

    bus.handle.side_effect = handle
    access = mock.create_autospec(ServiceClientProjectAccessService, instance=True)
    technologies = mock.create_autospec(TechnologiesQueryService, instance=True)
    technologies.get_technology.return_value = technology.Technology(technologyId="tech-1", technologyName="Ubuntu")
    return bootstrapper.Dependencies.model_construct(
        command_bus=bus,
        products_query_service=products,
        project_access_service=access,
        technologies_query_service=technologies,
        idempotency_service=FakeIdempotencyService(),
    )


def _invoke(dependencies, event, context):
    with patch("app.publishing.entrypoints.s2s_api.bootstrapper.bootstrap", return_value=dependencies):
        from app.publishing.entrypoints.s2s_api import handler

        importlib.reload(handler)
        return handler.handler(event, context)


def _body(response):
    return json.loads(response["body"]) if response.get("body") else None


def _create(dependencies, client_event, lambda_context, body=None, key=KEY):
    request = body or {
        "productName": "Example workbench arm64",
        "productType": "WORKBENCH",
        "technologyId": "tech-1",
    }
    headers = {"Idempotency-Key": key} if key else {}
    event = client_event("POST", BASE, json.dumps(request), headers=headers, scopes=[WRITE])
    return _invoke(dependencies, event, lambda_context)


def _commands(dependencies, kind):
    return [c.args[0] for c in dependencies.command_bus.handle.call_args_list if isinstance(c.args[0], kind)]


def test_create_resolves_the_technology_and_runs_once_for_a_retried_key(client_event, lambda_context):
    dependencies = _dependencies()

    first = _create(dependencies, client_event, lambda_context)
    retried = _create(dependencies, client_event, lambda_context)

    assert first["statusCode"] == 201
    assert retried["statusCode"] == 201
    assert _body(first) == _body(retried)
    assert _body(first)["productId"].startswith("prod-")
    (command,) = _commands(dependencies, create_product_command.CreateProductCommand)
    assert command.technologyName.value == "Ubuntu"
    assert command.productDescription.value == ""
    assert command.userId.value == "service:client-1"
    dependencies.project_access_service.require_access.assert_called_with("client-1", "proj-12345")


def test_create_with_the_same_key_and_another_body_is_a_conflict(client_event, lambda_context):
    dependencies = _dependencies()
    _create(dependencies, client_event, lambda_context)

    other = _create(
        dependencies,
        client_event,
        lambda_context,
        {"productName": "Other", "productType": "WORKBENCH", "technologyId": "tech-1"},
    )

    assert other["statusCode"] == 409
    assert _body(other)["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(_commands(dependencies, create_product_command.CreateProductCommand)) == 1


def test_create_needs_an_idempotency_key(client_event, lambda_context):
    response = _create(_dependencies(), client_event, lambda_context, key=None)

    assert response["statusCode"] == 400
    assert _body(response)["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_create_with_an_unknown_technology_fails_and_the_failure_is_replayed(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.technologies_query_service.get_technology.return_value = None

    first = _create(dependencies, client_event, lambda_context)
    dependencies.technologies_query_service.get_technology.return_value = technology.Technology(
        technologyId="tech-1", technologyName="Ubuntu"
    )
    retried = _create(dependencies, client_event, lambda_context)

    assert first["statusCode"] == retried["statusCode"] == 422
    assert _body(first)["code"] == _body(retried)["code"] == "TECHNOLOGY_NOT_FOUND"
    assert _commands(dependencies, create_product_command.CreateProductCommand) == []


def test_create_with_an_invalid_name_is_a_domain_failure(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.command_bus.handle.side_effect = domain_exception.DomainException("Product name should be ...")

    response = _create(dependencies, client_event, lambda_context)

    assert response["statusCode"] == 422
    assert _body(response)["code"] == "DOMAIN_VALIDATION_FAILED"


def test_create_rejects_unknown_fields(client_event, lambda_context):
    response = _create(
        _dependencies(),
        client_event,
        lambda_context,
        {"productName": "x", "productType": "WORKBENCH", "technologyId": "tech-1", "technologyName": "x"},
    )

    assert response["statusCode"] == 400
    assert _body(response)["code"] == "INVALID_REQUEST"


def test_get_returns_the_product_and_404_when_missing(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.products_query_service.add()

    found = _invoke(dependencies, client_event("GET", f"{BASE}/prod-12345678", scopes=[READ]), lambda_context)
    missing = _invoke(dependencies, client_event("GET", f"{BASE}/prod-missing1", scopes=[READ]), lambda_context)

    assert found["statusCode"] == 200
    assert _body(found) == {
        "projectId": "proj-12345",
        "productId": "prod-12345678",
        "productName": "Example workbench arm64",
        "productType": "WORKBENCH",
        "productDescription": "Example product",
        "technologyId": "tech-1",
        "technologyName": "Ubuntu",
        "status": "CREATED",
        "recommendedVersionId": None,
        "availableStages": ["DEV"],
        "createDate": "2026-09-29T00:00:00+00:00",
        "lastUpdateDate": "2026-09-29T00:00:00+00:00",
    }
    assert missing["statusCode"] == 404
    assert _body(missing)["code"] == "NOT_FOUND"


def test_a_product_of_another_project_is_not_found(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.products_query_service.add(project_id="proj-other")

    response = _invoke(dependencies, client_event("GET", f"{BASE}/prod-12345678", scopes=[READ]), lambda_context)

    assert response["statusCode"] == 404


def test_list_returns_the_projects_products(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.products_query_service.add()
    dependencies.products_query_service.add("prod-other001", project_id="proj-other")

    response = _invoke(dependencies, client_event("GET", BASE, scopes=[READ]), lambda_context)

    assert response["statusCode"] == 200
    assert [p["productId"] for p in _body(response)["products"]] == ["prod-12345678"]


def test_repeated_put_converges(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.products_query_service.add()
    body = json.dumps({"productName": "Example workbench", "productDescription": "Renamed"})

    responses = [
        _invoke(dependencies, client_event("PUT", f"{BASE}/prod-12345678", body, scopes=[WRITE]), lambda_context)
        for _ in range(2)
    ]

    assert [r["statusCode"] for r in responses] == [200, 200]
    assert _body(responses[0]) == _body(responses[1])
    assert _body(responses[1])["productName"] == "Example workbench"
    assert _body(responses[1])["productDescription"] == "Renamed"


def test_put_on_a_missing_product_is_404_and_on_an_archived_one_409(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.products_query_service.add(status=product.ProductStatus.Archived)
    body = json.dumps({"productName": "x"})

    missing = _invoke(dependencies, client_event("PUT", f"{BASE}/prod-missing1", body, scopes=[WRITE]), lambda_context)
    archived = _invoke(dependencies, client_event("PUT", f"{BASE}/prod-12345678", body, scopes=[WRITE]), lambda_context)

    assert missing["statusCode"] == 404
    assert archived["statusCode"] == 409
    assert _commands(dependencies, update_product_command.UpdateProductCommand) == []


def test_delete_archives_once_and_is_idempotent(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.products_query_service.add()
    path = f"{BASE}/prod-12345678"

    started = _invoke(dependencies, client_event("DELETE", path, scopes=[WRITE]), lambda_context)
    repeated = _invoke(dependencies, client_event("DELETE", path, scopes=[WRITE]), lambda_context)
    dependencies.products_query_service.items["prod-12345678"].status = product.ProductStatus.Archived
    done = _invoke(dependencies, client_event("DELETE", path, scopes=[WRITE]), lambda_context)
    missing = _invoke(dependencies, client_event("DELETE", f"{BASE}/prod-missing1", scopes=[WRITE]), lambda_context)

    assert [started["statusCode"], repeated["statusCode"], done["statusCode"], missing["statusCode"]] == [
        202,
        202,
        204,
        204,
    ]
    assert started["multiValueHeaders"]["Retry-After"] == ["5"]
    assert len(_commands(dependencies, archive_product_command.ArchiveProductCommand)) == 1


@pytest.mark.parametrize(
    "method,path,scope",
    [
        ("GET", BASE, WRITE),
        ("GET", f"{BASE}/prod-12345678", WRITE),
        ("PUT", f"{BASE}/prod-12345678", READ),
        ("DELETE", f"{BASE}/prod-12345678", READ),
    ],
)
def test_each_operation_needs_its_scope(method, path, scope, client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.products_query_service.add()
    body = json.dumps({"productName": "x"}) if method == "PUT" else None

    response = _invoke(dependencies, client_event(method, path, body, scopes=[scope]), lambda_context)

    assert response["statusCode"] == 403
    assert _body(response)["code"] == "INSUFFICIENT_SCOPE"
    dependencies.command_bus.handle.assert_not_called()


@pytest.mark.parametrize(
    "error,status,code",
    [
        (s2s_exception.ProjectAccessDenied(), 403, "PROJECT_ACCESS_DENIED"),
        (s2s_exception.ProjectAccessUnavailable(), 503, "PROJECT_ACCESS_UNAVAILABLE"),
    ],
)
def test_the_client_needs_a_project_assignment(error, status, code, client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.project_access_service.require_access.side_effect = error

    response = _create(dependencies, client_event, lambda_context)

    assert response["statusCode"] == status
    assert _body(response)["code"] == code
    assert dependencies.idempotency_service.records == {}
