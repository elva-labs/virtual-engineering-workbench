import assertpy
import pytest
from freezegun import freeze_time

from app.publishing.domain.command_handlers import update_product_command_handler
from app.publishing.domain.commands import update_product_command
from app.publishing.domain.exceptions import domain_exception
from app.publishing.domain.model import product
from app.publishing.domain.value_objects import (
    product_description_value_object,
    product_id_value_object,
    product_name_value_object,
    project_id_value_object,
    user_id_value_object,
)


def _command(name="Example workbench", description="Example product"):
    return update_product_command.UpdateProductCommand(
        projectId=project_id_value_object.from_str("proj-12345"),
        productId=product_id_value_object.from_str("prod-12345abc"),
        productName=product_name_value_object.from_str(name),
        productDescription=product_description_value_object.from_str(description),
        userId=user_id_value_object.from_str("service:terraform"),
    )


def _product(status=product.ProductStatus.Created, name="My product", description=None):
    return product.Product(
        projectId="proj-12345",
        productId="prod-12345abc",
        technologyId="tech-12345",
        technologyName="Test technology",
        status=status,
        productName=name,
        productDescription=description,
        productType=product.ProductType.Workbench,
        createDate="2023-07-13T00:00:00+00:00",
        lastUpdateDate="2023-07-13T00:00:00+00:00",
        createdBy="T000001",
        lastUpdatedBy="T000001",
    )


@freeze_time("2026-09-29")
def test_update_product_changes_name_and_description(mock_unit_of_work, mock_products_repo):
    mock_products_repo.get.return_value = _product()

    update_product_command_handler.handle(_command(), mock_unit_of_work)

    mock_products_repo.update_attributes.assert_called_once_with(
        pk=product.ProductPrimaryKey(projectId="proj-12345", productId="prod-12345abc"),
        productName="Example workbench",
        productDescription="Example product",
        lastUpdatedBy="service:terraform",
        lastUpdateDate="2026-09-29T00:00:00+00:00",
    )
    mock_unit_of_work.commit.assert_called_once()


def test_update_product_with_the_same_values_writes_nothing(mock_unit_of_work, mock_products_repo):
    mock_products_repo.get.return_value = _product(name="Example workbench", description="Example product")

    update_product_command_handler.handle(_command(), mock_unit_of_work)

    mock_products_repo.update_attributes.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()


@pytest.mark.parametrize("status", [product.ProductStatus.Archiving, product.ProductStatus.Archived])
def test_update_product_rejects_archived_products(status, mock_unit_of_work, mock_products_repo):
    mock_products_repo.get.return_value = _product(status=status)

    assertpy.assert_that(update_product_command_handler.handle).raises(
        domain_exception.DomainException
    ).when_called_with(_command(), mock_unit_of_work)
    mock_products_repo.update_attributes.assert_not_called()


def test_update_product_rejects_missing_products(mock_unit_of_work, mock_products_repo):
    mock_products_repo.get.return_value = None

    assertpy.assert_that(update_product_command_handler.handle).raises(
        domain_exception.DomainException
    ).when_called_with(_command(), mock_unit_of_work)
