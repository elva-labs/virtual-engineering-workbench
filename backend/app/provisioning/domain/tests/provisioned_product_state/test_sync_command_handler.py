import pytest
from freezegun import freeze_time

from app.provisioning.domain.command_handlers.provisioned_product_state import sync
from app.provisioning.domain.commands.provisioned_product_state import sync_provisioned_product_state_command
from app.provisioning.domain.events.provisioned_product_sync import provisioned_product_status_out_of_sync
from app.provisioning.domain.exceptions import not_found_exception
from app.provisioning.domain.model import product_status, provisioned_product_details


@pytest.fixture()
def mock_command():
    return sync_provisioned_product_state_command.SyncProvisionedProductStateCommand()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exists_only_in_repo_should_terminate(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = None

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Provisioning,
            newStatus=product_status.ProductStatus.Terminated,
        )
    )
    mock_unit_of_work.commit.assert_not_called()


@pytest.mark.parametrize(
    "status,update_time",
    [
        (product_status.ProductStatus.Updating, "2023-12-05T02:00:00+00:00"),
        (product_status.ProductStatus.Provisioning, "2023-12-05T04:40:00+00:00"),
        (product_status.ProductStatus.ConfigurationInProgress, "2023-12-05T04:40:00+00:00"),
        (product_status.ProductStatus.Running, "2023-12-05T04:52:00+00:00"),
    ],
)
@freeze_time("2023-12-05T05:00:00+00:00")
def test_sync_workbenches_when_exists_only_in_repo_but_new_should_not_terminate(
    status,
    update_time,
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = None
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(last_update_date=update_time, status=status)
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [product_status.ProductStatus.Running, product_status.ProductStatus.Stopped],
)
@freeze_time("2023-12-05T05:00:00+00:00")
def test_sync_workbenches_when_settled_and_gone_from_service_catalog_should_terminate_after_10_minutes(
    status,
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE: a settled workbench whose Service Catalog product was removed outside VEW; there is no
    # launch race, so the sync reports it after 10 minutes instead of 30.
    mock_products_srv.get_provisioned_product_details.return_value = None
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(last_update_date="2023-12-05T04:45:00+00:00", status=status)
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=status,
            newStatus=product_status.ProductStatus.Terminated,
        )
    )


@pytest.mark.parametrize(
    "update_time,expect_terminated",
    [
        ("2023-12-05T04:50:00+00:00", True),
        ("2023-12-05T04:58:00+00:00", False),
    ],
)
@freeze_time("2023-12-05T05:00:00+00:00")
def test_sync_workbenches_when_deprovisioning_and_gone_from_service_catalog_should_terminate_after_5_minutes(
    update_time,
    expect_terminated,
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE: a launch that failed before its stack existed was removed; Service Catalog forgot it and
    # no stack event will ever arrive.
    mock_products_srv.get_provisioned_product_details.return_value = None
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(last_update_date=update_time, status=product_status.ProductStatus.Deprovisioning)
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    if expect_terminated:
        mock_message_bus.publish.assert_called_once_with(
            provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
                provisionedProductId="pp-123",
                oldStatus=product_status.ProductStatus.Deprovisioning,
                newStatus=product_status.ProductStatus.Terminated,
            )
        )
    else:
        mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exists_only_on_repo_but_already_terminated_should_ignore(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = None
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(status=product_status.ProductStatus.Terminated)
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_changed_should_publish(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Terminated,
            sc_provisioned_product_id="sc-pp-123",
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Terminated,
            newStatus=product_status.ProductStatus.Stopped,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_called_with(
        user_id="VEWProvisioningBCSync",
        aws_account_id="001234567890",
        region="us-east-1",
        instance_id="i-01234567890abcdef",
    )
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_changed_should_fetch_latest_and_publish(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Starting,
            sc_provisioned_product_id="sc-pp-123",
            instance_id=None,
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Starting,
            newStatus=product_status.ProductStatus.Stopped,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_called_with(
        user_id="VEWProvisioningBCSync",
        aws_account_id="001234567890",
        region="us-east-1",
        instance_id="i-1234567890",
    )
    mock_products_srv.get_provisioned_product_outputs.assert_called_with(
        user_id="VEWProvisioningBCSync",
        aws_account_id="001234567890",
        region="us-east-1",
        provisioned_product_id="sc-pp-123",
    )


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_provisioning_error_should_update_status(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[], Status=product_status.ServiceCatalogStatus.Error, Id="sc-pp-123", ProvisioningArtifactId="pa-123"
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Provisioning,
            sc_provisioned_product_id="sc-pp-123",
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Provisioning,
            newStatus=product_status.ProductStatus.ProvisioningError,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_both_provisioning_error_should_skip(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[], Status=product_status.ServiceCatalogStatus.Error, Id="sc-pp-123", ProvisioningArtifactId="pa-123"
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.ProvisioningError,
            sc_provisioned_product_id="sc-pp-123",
            instance_id=None,
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_deprovisioning_error_should_update_status(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[], Status=product_status.ServiceCatalogStatus.Tainted, Id="sc-pp-123", ProvisioningArtifactId="pa-123"
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Deprovisioning,
            sc_provisioned_product_id="sc-pp-123",
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Deprovisioning,
            newStatus=product_status.ProductStatus.ProvisioningError,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_changed_should_ignore_instances_wo_instance_id_in_the_output(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Starting,
            sc_provisioned_product_id="sc-pp-123",
            instance_id=None,
        )
    ]
    mock_products_srv.get_provisioned_product_outputs.return_value = []

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_under_change_should_not_try_fetch_outputs(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.UnderChange,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Starting,
            sc_provisioned_product_id="sc-pp-123",
            instance_id=None,
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_not_changed_should_not_update_repo(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Stopped,
            sc_provisioned_product_id="sc-pp-123",
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_instance_mgmt_srv.get_instance_state.assert_called_with(
        aws_account_id="001234567890",
        instance_id="i-01234567890abcdef",
        region="us-east-1",
        user_id="VEWProvisioningBCSync",
    )
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_product_is_available_but_wb_is_updating_should_update_status(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Updating,
            sc_provisioned_product_id="sc-pp-123",
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Updating,
            newStatus=product_status.ProductStatus.Stopped,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_called_with(
        user_id="VEWProvisioningBCSync",
        aws_account_id="001234567890",
        region="us-east-1",
        instance_id="i-1234567890",
    )
    mock_products_srv.get_provisioned_product_outputs.assert_called_with(
        user_id="VEWProvisioningBCSync",
        aws_account_id="001234567890",
        region="us-east-1",
        provisioned_product_id="sc-pp-123",
    )


@freeze_time("2024-01-24")
def test_sync_workbenches_when_product_is_tainted_but_wb_is_upgrading_should_update_status(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Tainted,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Updating,
            sc_provisioned_product_id="sc-pp-123",
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Updating,
            newStatus=product_status.ProductStatus.ProvisioningError,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_stack_does_not_exist_should_set_to_provisioning_error_status(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_products_srv.get_provisioned_product_outputs.side_effect = not_found_exception.NotFoundException()

    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.Terminated,
            sc_provisioned_product_id="sc-pp-123",
            instance_id=None,
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.Terminated,
            newStatus=product_status.ProductStatus.ProvisioningError,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_called_once()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_provisioning_error_in_db_should_ignore(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Tainted,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )

    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.ProvisioningError,
            sc_provisioned_product_id="sc-pp-123",
            instance_id=None,
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_configuration_failed_should_skip(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.ConfigurationFailed,
            sc_provisioned_product_id="sc-pp-123",
            instance_id=None,
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_not_called()
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()


@freeze_time("2024-01-24")
def test_sync_workbenches_when_exist_both_and_stuck_in_configuration_in_progress_should_update_to_configuration_failed(
    mock_command,
    mock_publisher,
    mock_logger,
    mock_virtual_targets_qs,
    mock_products_srv,
    mock_instance_mgmt_srv,
    mock_message_bus,
    mock_unit_of_work,
    get_virtual_target,
):
    # ARRANGE
    mock_products_srv.get_provisioned_product_details.return_value = (
        provisioned_product_details.ProvisionedProductDetails(
            Tags=[],
            Status=product_status.ServiceCatalogStatus.Available,
            Id="sc-pp-123",
            ProvisioningArtifactId="pa-123",
        )
    )
    mock_virtual_targets_qs.get_all_provisioned_products.return_value = [
        get_virtual_target(
            status=product_status.ProductStatus.ConfigurationInProgress,
            sc_provisioned_product_id="sc-pp-123",
        )
    ]

    # ACT
    sync.handle(
        command=mock_command,
        publisher=mock_publisher,
        logger=mock_logger,
        pp_qry_srv=mock_virtual_targets_qs,
        products_srv=mock_products_srv,
        instance_mgmt_srv=mock_instance_mgmt_srv,
    )

    # ASSERT
    mock_message_bus.publish.assert_called_once_with(
        provisioned_product_status_out_of_sync.ProvisionedProductStatusOutOfSync(
            provisionedProductId="pp-123",
            oldStatus=product_status.ProductStatus.ConfigurationInProgress,
            newStatus=product_status.ProductStatus.ConfigurationFailed,
        )
    )
    mock_unit_of_work.commit.assert_not_called()
    mock_instance_mgmt_srv.get_instance_state.assert_not_called()
    mock_products_srv.get_provisioned_product_outputs.assert_not_called()
