"""Platform products (docs/platform-products.md): every program lists and resolves the releasing program's."""

from app.provisioning.adapters.query_services import dynamodb_products_query_service
from app.provisioning.adapters.tests import conftest
from app.provisioning.adapters.tests.test_dynamodb_products_query_service import fill_db_with_products
from app.provisioning.domain.read_models import product

PLATFORM = "prog-73488"


def _product(project_id, product_id, scope=product.ProductScope.Program):
    return product.Product(
        projectId=project_id,
        productId=product_id,
        technologyId="tech-1",
        technologyName="Ubuntu DCV",
        productName="Saab default workbench",
        productType=product.ProductType.Workbench,
        availableStages=[product.ProductStage.DEV, product.ProductStage.PROD],
        availableRegions=["eu-north-1"],
        lastUpdateDate="2026-10-01T00:00:00+00:00",
        scope=scope,
    )


def _service(mock_dynamodb, platform_program_id=PLATFORM):
    return dynamodb_products_query_service.DynamoDBProductsQueryService(
        table_name=conftest.TEST_TABLE_NAME,
        dynamodb_client=mock_dynamodb.meta.client,
        platform_program_id=platform_program_id,
    )


def _fill(table):
    fill_db_with_products(
        table,
        [
            _product("proj-a", "prod-team1"),
            _product(PLATFORM, "prod-default1", product.ProductScope.Platform),
            _product(PLATFORM, "prod-internal", product.ProductScope.Program),
            _product("proj-b", "prod-other"),
        ],
    )


def test_a_program_lists_its_own_and_the_platform_products(mock_dynamodb, backend_app_dynamodb_table):
    _fill(backend_app_dynamodb_table)

    listed = _service(mock_dynamodb).get_products(project_id="proj-a")

    assert sorted(p.productId for p in listed) == ["prod-default1", "prod-team1"]


def test_the_releasing_program_lists_its_products_once(mock_dynamodb, backend_app_dynamodb_table):
    _fill(backend_app_dynamodb_table)

    listed = _service(mock_dynamodb).get_products(project_id=PLATFORM)

    assert sorted(p.productId for p in listed) == ["prod-default1", "prod-internal"]


def test_a_program_resolves_a_platform_product_but_not_another_programs(mock_dynamodb, backend_app_dynamodb_table):
    _fill(backend_app_dynamodb_table)
    service = _service(mock_dynamodb)

    assert service.get_product(project_id="proj-a", product_id="prod-default1").projectId == PLATFORM
    assert service.get_product(project_id="proj-a", product_id="prod-internal") is None
    assert service.get_product(project_id="proj-a", product_id="prod-other") is None


def test_without_a_releasing_program_nothing_is_shared(mock_dynamodb, backend_app_dynamodb_table):
    _fill(backend_app_dynamodb_table)
    service = _service(mock_dynamodb, platform_program_id="")

    assert [p.productId for p in service.get_products(project_id="proj-a")] == ["prod-team1"]
    assert service.get_product(project_id="proj-a", product_id="prod-default1") is None
