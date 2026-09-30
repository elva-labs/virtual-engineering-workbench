from datetime import datetime, timezone

from app.publishing.domain.commands import update_product_command
from app.publishing.domain.exceptions import domain_exception
from app.publishing.domain.model import product
from app.shared.adapters.unit_of_work_v2 import unit_of_work

# Name and description only: type and technology decide the product's template and portfolios, so
# changing them means a new product.
UPDATABLE_STATUSES = (product.ProductStatus.Created, product.ProductStatus.Paused)


def handle(
    command: update_product_command.UpdateProductCommand,
    unit_of_work: unit_of_work.UnitOfWork,
) -> None:
    pk = product.ProductPrimaryKey(projectId=command.projectId.value, productId=command.productId.value)
    with unit_of_work:
        repository = unit_of_work.get_repository(product.ProductPrimaryKey, product.Product)
        product_entity: product.Product | None = repository.get(pk=pk)
        if product_entity is None:
            raise domain_exception.DomainException(f"Product {command.productId.value} not found.")
        if product_entity.status not in UPDATABLE_STATUSES:
            raise domain_exception.DomainException(
                f"Only products with status {', '.join(UPDATABLE_STATUSES)} can be updated."
            )
        if (
            product_entity.productName == command.productName.value
            and (product_entity.productDescription or "") == command.productDescription.value
        ):
            return

        repository.update_attributes(
            pk=pk,
            productName=command.productName.value,
            productDescription=command.productDescription.value,
            lastUpdatedBy=command.userId.value,
            lastUpdateDate=datetime.now(timezone.utc).isoformat(),
        )
        unit_of_work.commit()
