from openapi_spec_validator import validate


def _operations(api_schema):
    for path, operations in api_schema["paths"].items():
        for method, operation in operations.items():
            yield path, method, operation


def test_schema_is_valid(api_schema):
    validate(api_schema)


def test_every_operation_has_its_own_scope(api_schema):
    for path, method, operation in _operations(api_schema):
        (requirement,) = operation["security"]
        if "/versions" in path:
            expected = "version.read" if method == "get" else "version.promote"
        else:
            expected = "product.read" if method == "get" else "product.write"
        assert requirement == {"ClientCredentials": [f"clients/publishing/{expected}"]}, (method, path)


def test_create_requires_an_idempotency_key(api_schema):
    create = api_schema["paths"]["/projects/{projectId}/products"]["post"]

    assert {"$ref": "#/components/parameters/IdempotencyKey"} in create["parameters"]


def test_statuses_match_the_domain(api_schema):
    from app.publishing.domain.model import product

    statuses = api_schema["components"]["schemas"]["ProductStatus"]["enum"]
    assert statuses == [status.value for status in product.ProductStatus]
    assert api_schema["components"]["schemas"]["ProductType"]["enum"] == product.ProductType.list()
