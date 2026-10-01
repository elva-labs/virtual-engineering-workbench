from datetime import datetime, timezone
from http import HTTPStatus

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types

from app.publishing.domain.commands import archive_product_command, create_product_command, update_product_command
from app.publishing.domain.exceptions import s2s_exception
from app.publishing.domain.model import product
from app.publishing.domain.value_objects import (
    product_description_value_object,
    product_id_value_object,
    product_name_value_object,
    product_type_value_object,
    project_id_value_object,
    tech_id_value_object,
    tech_name_value_object,
    user_id_value_object,
)
from app.publishing.entrypoints.s2s_api import bootstrapper, common, idempotency
from app.publishing.entrypoints.s2s_api.model import api_model

tracer = Tracer()

READ_SCOPE = "clients/publishing/product.read"
WRITE_SCOPE = "clients/publishing/product.write"

GONE = (product.ProductStatus.Archiving, product.ProductStatus.Archived)


def _product_response(entity: product.Product) -> api_model.Product:
    return api_model.Product(
        projectId=entity.projectId,
        productId=entity.productId,
        productName=entity.productName,
        productType=entity.productType.value,
        productDescription=entity.productDescription or "",
        technologyId=entity.technologyId,
        technologyName=entity.technologyName,
        status=entity.status.value,
        recommendedVersionId=entity.recommendedVersionId,
        availableStages=[stage.value for stage in entity.availableStages or []],
        scope=str(entity.scope),
        createDate=entity.createDate,
        lastUpdateDate=entity.lastUpdateDate,
    )


def _json(status: HTTPStatus, body, headers: dict | None = None) -> api_gateway.Response:
    return api_gateway.Response(
        status_code=int(status),
        body=body,
        headers={**common.NO_STORE, **(headers or {})},
        content_type=content_types.APPLICATION_JSON,
    )


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()

    def find(project_id: str, product_id: str) -> product.Product | None:
        return dependencies.products_query_service.get_product(project_id, product_id)

    def product_in_project(project_id: str, product_id: str) -> product.Product:
        entity = find(project_id, product_id)
        if entity is None:
            raise s2s_exception.ResourceNotFound()
        return entity

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/products")
    def list_products(project_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        products = dependencies.products_query_service.get_products(project_id)
        return _json(HTTPStatus.OK, api_model.ProductPage(products=[_product_response(p) for p in products]))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.post("/projects/<project_id>/products")
    def create_product(project_id: str, request: api_model.CreateProductRequest):
        client = common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        if request.scope == product.ProductScope.Platform.value and project_id != dependencies.platform_program_id:
            raise s2s_exception.ReleasingProjectOnly()
        scope = common.idempotency_scope(router, client, project_id, "CREATE_PRODUCT")

        def create(product_id: str) -> idempotency.StoredCreateResponse:
            technology = dependencies.technologies_query_service.get_technology(project_id, request.technologyId)
            if technology is None:
                raise s2s_exception.TechnologyNotFound()
            dependencies.command_bus.handle(
                create_product_command.CreateProductCommand(
                    projectId=project_id_value_object.from_str(project_id),
                    productId=product_id_value_object.from_str(product_id),
                    productName=product_name_value_object.from_str(request.productName),
                    productType=product_type_value_object.from_str(request.productType),
                    productDescription=product_description_value_object.from_str(request.productDescription),
                    technologyId=tech_id_value_object.from_str(technology.technologyId),
                    technologyName=tech_name_value_object.from_str(technology.technologyName),
                    userId=user_id_value_object.from_str(f"service:{client}"),
                    scope=product.ProductScope(request.scope),
                )
            )
            return idempotency.StoredCreateResponse(HTTPStatus.CREATED, {"productId": product_id})

        result = idempotency.execute_create(
            service=dependencies.idempotency_service,
            scope=scope,
            request=request,
            resource_id=product_id_value_object.generate_product_id(),
            resource_exists=lambda product_id: find(project_id, product_id) is not None,
            response_for_id=lambda product_id: idempotency.StoredCreateResponse(
                HTTPStatus.CREATED, {"productId": product_id}
            ),
            create=create,
            now=datetime.now(timezone.utc),
        )
        return _json(HTTPStatus(result.status_code), api_model.CreateProductResponse.model_validate(result.body))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/products/<product_id>")
    def get_product(project_id: str, product_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        return _json(HTTPStatus.OK, _product_response(product_in_project(project_id, product_id)))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put("/projects/<project_id>/products/<product_id>")
    def update_product(project_id: str, product_id: str, request: api_model.UpdateProductRequest):
        client = common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        if product_in_project(project_id, product_id).status in GONE:
            raise s2s_exception.ResourceConflict()
        dependencies.command_bus.handle(
            update_product_command.UpdateProductCommand(
                projectId=project_id_value_object.from_str(project_id),
                productId=product_id_value_object.from_str(product_id),
                productName=product_name_value_object.from_str(request.productName),
                productDescription=product_description_value_object.from_str(request.productDescription),
                userId=user_id_value_object.from_str(f"service:{client}"),
            )
        )
        return _json(HTTPStatus.OK, _product_response(product_in_project(project_id, product_id)))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.delete("/projects/<project_id>/products/<product_id>")
    def archive_product(project_id: str, product_id: str):
        client = common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        existing = find(project_id, product_id)
        # Archiving unpublishes the product from every account; it has history, so it is not removed.
        if existing is None or existing.status == product.ProductStatus.Archived:
            return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT, headers=common.NO_STORE)
        if existing.status != product.ProductStatus.Archiving:
            dependencies.command_bus.handle(
                archive_product_command.ArchiveProductCommand(
                    projectId=project_id_value_object.from_str(project_id),
                    productId=product_id_value_object.from_str(product_id),
                    archivedBy=user_id_value_object.from_str(f"service:{client}"),
                )
            )
        return _json(HTTPStatus.ACCEPTED, {"productId": product_id}, {"Retry-After": "5"})

    return router
