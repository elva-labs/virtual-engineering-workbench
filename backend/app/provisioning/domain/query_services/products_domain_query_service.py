from app.provisioning.domain.model import stage_access
from app.provisioning.domain.ports import (
    networking_query_service,
    products_query_service,
)
from app.provisioning.domain.read_models import product
from app.provisioning.domain.value_objects import (
    product_id_value_object,
    product_type_value_object,
    project_id_value_object,
    user_role_value_object,
)


class ProductsDomainQueryService:
    def __init__(
        self,
        products_qry_srv: products_query_service.ProductsQueryService,
        networking_qry_srv: networking_query_service.NetworkingQueryService,
    ) -> None:
        self._products_qry_srv = products_qry_srv
        self._networking_qry_srv = networking_qry_srv

    def get_available_products(
        self,
        project_id: project_id_value_object.ProjectIdValueObject,
        user_roles: list[user_role_value_object.UserRoleValueObject],
        product_type: product_type_value_object.ProductTypeValueObject,
        product_id_filter: list[product_id_value_object.ProductIdValueObject] = [],
    ) -> list[product.Product]:
        # Decide which stages does the user role has access to
        allowed_stages = [
            product.ProductStage(stage)
            for stage in stage_access.allowed_stages([user_role.value for user_role in user_roles])
        ]

        # Fetch the products available in these stages
        products = self._products_qry_srv.get_products(
            project_id=project_id.value,
            available_stages=allowed_stages,
            product_type=product_type.value,
        )

        # filter products by product_id
        if product_id_filter:
            filter_values: list[str] = [f.value for f in product_id_filter]
            products = [prod for prod in products if prod.productId in filter_values]

        # Update the stages to return only stages allowed for the user role
        for prod in products:
            prod.availableStages = [stage for stage in prod.availableStages if stage in allowed_stages]

        return products
