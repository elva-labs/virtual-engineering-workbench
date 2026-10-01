"""Platform products (docs/platform-products.md): only the releasing program creates them."""

from app.publishing.domain.commands import create_product_command
from app.publishing.domain.model import product
from app.publishing.entrypoints.s2s_api.tests.test_products import _body, _commands, _create, _dependencies

BODY = {"productName": "Saab default workbench", "productType": "WORKBENCH", "technologyId": "tech-1"}


def test_the_releasing_program_creates_a_platform_product(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.platform_program_id = "proj-12345"

    response = _create(dependencies, client_event, lambda_context, {**BODY, "scope": "PLATFORM"})

    assert response["statusCode"] == 201
    (command,) = _commands(dependencies, create_product_command.CreateProductCommand)
    assert command.scope == product.ProductScope.Platform


def test_another_program_cannot_create_a_platform_product(client_event, lambda_context):
    dependencies = _dependencies()
    dependencies.platform_program_id = "prog-releasing"

    response = _create(dependencies, client_event, lambda_context, {**BODY, "scope": "PLATFORM"})

    assert response["statusCode"] == 409
    assert _body(response)["code"] == "RELEASING_PROJECT_ONLY"
    assert _commands(dependencies, create_product_command.CreateProductCommand) == []


def test_without_a_releasing_program_nobody_creates_platform_products(client_event, lambda_context):
    dependencies = _dependencies()

    response = _create(dependencies, client_event, lambda_context, {**BODY, "scope": "PLATFORM"})

    assert response["statusCode"] == 409


def test_a_program_product_is_the_default_scope(client_event, lambda_context):
    dependencies = _dependencies()

    _create(dependencies, client_event, lambda_context)

    (command,) = _commands(dependencies, create_product_command.CreateProductCommand)
    assert command.scope == product.ProductScope.Program
