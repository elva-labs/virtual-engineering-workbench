"""a launch only tries AZs that offer the instance type, and moves on from one that answers
EC2 Unsupported like it does from a capacity error."""

from unittest import mock

import assertpy
from freezegun import freeze_time

from app.provisioning.domain.command_handlers.product_provisioning import fail_launch, provision_product
from app.provisioning.domain.commands.product_provisioning import (
    fail_product_launch_command,
    provision_product_command,
)
from app.provisioning.domain.events.product_provisioning import insufficient_capacity_reached
from app.provisioning.domain.model import network_subnet, product_status, provisioning_parameter
from app.provisioning.domain.model.workbench_failure import FailureCode, classify_reason
from app.provisioning.domain.value_objects import ip_address_value_object, provisioned_product_id_value_object


def _subnet(subnet_id, az, ips):
    return network_subnet.NetworkSubnet(
        subnet_id=subnet_id,
        available_ip_address_count=ips,
        availability_zone=az,
        tags=[],
        cidr_block="100.96.0.0/19",
        vpc_id="vpc-123",
    )


# As the real selector returns them, most free addresses first: az-3 is new and empty, so without the
# offerings it would be picked first.
SUBNETS = [_subnet("s-3", "az-3", 250), _subnet("s-2", "az-2", 60), _subnet("s-1", "az-1", 50)]


def _params(instance_type="g6.xlarge"):
    return [
        provisioning_parameter.ProvisioningParameter(key="InstanceType", value=instance_type),
        provisioning_parameter.ProvisioningParameter(
            key="SubnetId", isTechnicalParameter=True, parameterType="AWS::EC2::Subnet::Id"
        ),
    ]


def _provision(deps, product):
    deps["qs"].get_by_id.return_value = product
    selector = mock.MagicMock(side_effect=lambda **kwargs: list(SUBNETS))
    provision_product.handle(
        command=provision_product_command.ProvisionProductCommand(
            provisioned_product_id=provisioned_product_id_value_object.from_str("pp-123"),
            user_ip_address=ip_address_value_object.from_str("127.0.0.1"),
        ),
        publisher=deps["publisher"],
        products_srv=deps["products"],
        virtual_targets_qs=deps["qs"],
        parameter_srv=deps["parameters"],
        instance_mgmt_srv=deps["instances"],
        logger=deps["logger"],
        spoke_account_vpc_id_param_name="/workbench/vpc/vpc-id",
        subnet_selector=selector,
        authorize_user_ip_address_param_value=False,
        uow=deps["uow"],
    )


def _deps(
    mock_publisher,
    mock_products_srv,
    mock_provisioned_products_qs,
    mock_parameter_srv,
    mock_instance_mgmt_srv,
    mock_logger,
    mock_unit_of_work,
):
    return {
        "publisher": mock_publisher,
        "products": mock_products_srv,
        "qs": mock_provisioned_products_qs,
        "parameters": mock_parameter_srv,
        "instances": mock_instance_mgmt_srv,
        "logger": mock_logger,
        "uow": mock_unit_of_work,
    }


def _saved(mock_provisioned_product_repo):
    call = mock_provisioned_product_repo.update_entity.call_args
    return call.args[1] if len(call.args) > 1 else call.kwargs.get("entity", list(call.kwargs.values())[-1])


@freeze_time("2023-12-06")
def test_launch_skips_an_az_that_does_not_offer_the_instance_type(
    mock_logger,
    mock_publisher,
    mock_products_srv,
    mock_provisioned_products_qs,
    mock_unit_of_work,
    mock_provisioned_product_repo,
    mock_parameter_srv,
    mock_instance_mgmt_srv,
    get_provisioned_product,
    mock_user_profile_repo,
):
    mock_user_profile_repo.get.return_value = None  # no preferred AZ: pick by free addresses
    mock_instance_mgmt_srv.get_offered_availability_zones.return_value = {"az-1", "az-2"}
    deps = _deps(
        mock_publisher,
        mock_products_srv,
        mock_provisioned_products_qs,
        mock_parameter_srv,
        mock_instance_mgmt_srv,
        mock_logger,
        mock_unit_of_work,
    )

    _provision(deps, get_provisioned_product(provisioning_parameters=_params()))

    sent = mock_products_srv.provision_product.call_args.kwargs["provisioning_parameters"]
    subnet = next(p for p in sent if p.key == "SubnetId")
    assertpy.assert_that(subnet.value).is_equal_to("s-2")
    assertpy.assert_that(_saved(mock_provisioned_product_repo).availabilityZonesTriggered).is_equal_to(["az-2"])
    mock_instance_mgmt_srv.get_offered_availability_zones.assert_called_with(
        user_id="T0011AA", aws_account_id="001234567890", region="us-east-1", instance_type="g6.xlarge"
    )


@freeze_time("2023-12-06")
def test_launch_uses_every_subnet_when_the_offerings_are_unknown(
    mock_logger,
    mock_publisher,
    mock_products_srv,
    mock_provisioned_products_qs,
    mock_unit_of_work,
    mock_provisioned_product_repo,
    mock_parameter_srv,
    mock_instance_mgmt_srv,
    get_provisioned_product,
    mock_user_profile_repo,
):
    mock_user_profile_repo.get.return_value = None  # no preferred AZ: pick by free addresses
    mock_instance_mgmt_srv.get_offered_availability_zones.return_value = None
    deps = _deps(
        mock_publisher,
        mock_products_srv,
        mock_provisioned_products_qs,
        mock_parameter_srv,
        mock_instance_mgmt_srv,
        mock_logger,
        mock_unit_of_work,
    )

    _provision(deps, get_provisioned_product(provisioning_parameters=_params()))

    sent = mock_products_srv.provision_product.call_args.kwargs["provisioning_parameters"]
    assertpy.assert_that(next(p for p in sent if p.key == "SubnetId").value).is_equal_to("s-3")


@freeze_time("2023-12-06")
def test_launch_fails_as_unsupported_when_no_az_offers_the_instance_type(
    mock_logger,
    mock_publisher,
    mock_products_srv,
    mock_provisioned_products_qs,
    mock_unit_of_work,
    mock_provisioned_product_repo,
    mock_parameter_srv,
    mock_instance_mgmt_srv,
    get_provisioned_product,
    mock_user_profile_repo,
):
    mock_user_profile_repo.get.return_value = None  # no preferred AZ: pick by free addresses
    mock_instance_mgmt_srv.get_offered_availability_zones.return_value = {"az-9"}
    deps = _deps(
        mock_publisher,
        mock_products_srv,
        mock_provisioned_products_qs,
        mock_parameter_srv,
        mock_instance_mgmt_srv,
        mock_logger,
        mock_unit_of_work,
    )

    _provision(deps, get_provisioned_product(provisioning_parameters=_params("p5.48xlarge")))

    mock_products_srv.provision_product.assert_not_called()
    saved = _saved(mock_provisioned_product_repo)
    assertpy.assert_that(saved.status).is_equal_to(product_status.ProductStatus.ProvisioningError)
    assertpy.assert_that(saved.statusReason).contains("p5.48xlarge").contains("az-1, az-2, az-3")
    assertpy.assert_that(classify_reason(saved.statusReason)).is_equal_to(FailureCode.UnsupportedInAz)


@freeze_time("2023-12-06")
def test_launch_moves_on_to_an_untried_az_even_when_tried_azs_are_no_longer_eligible(
    mock_logger,
    mock_publisher,
    mock_products_srv,
    mock_provisioned_products_qs,
    mock_unit_of_work,
    mock_provisioned_product_repo,
    mock_parameter_srv,
    mock_instance_mgmt_srv,
    get_provisioned_product,
    mock_user_profile_repo,
):
    mock_user_profile_repo.get.return_value = None  # no preferred AZ: pick by free addresses
    # az-3 failed before the offerings were known; az-1 failed on capacity; az-2 is still untried.
    mock_instance_mgmt_srv.get_offered_availability_zones.return_value = {"az-1", "az-2"}
    deps = _deps(
        mock_publisher,
        mock_products_srv,
        mock_provisioned_products_qs,
        mock_parameter_srv,
        mock_instance_mgmt_srv,
        mock_logger,
        mock_unit_of_work,
    )

    _provision(
        deps, get_provisioned_product(provisioning_parameters=_params(), availability_zones_triggered=["az-3", "az-1"])
    )

    sent = mock_products_srv.provision_product.call_args.kwargs["provisioning_parameters"]
    assertpy.assert_that(next(p for p in sent if p.key == "SubnetId").value).is_equal_to("s-2")


@freeze_time("2023-12-06")
def test_all_azs_tried_fails_as_capacity_unless_every_one_was_unsupported(
    mock_logger,
    mock_publisher,
    mock_products_srv,
    mock_provisioned_products_qs,
    mock_unit_of_work,
    mock_provisioned_product_repo,
    mock_parameter_srv,
    mock_instance_mgmt_srv,
    get_provisioned_product,
    mock_user_profile_repo,
):
    mock_user_profile_repo.get.return_value = None  # no preferred AZ: pick by free addresses
    deps = _deps(
        mock_publisher,
        mock_products_srv,
        mock_provisioned_products_qs,
        mock_parameter_srv,
        mock_instance_mgmt_srv,
        mock_logger,
        mock_unit_of_work,
    )
    tried = ["az-3", "az-2", "az-1"]

    product = get_provisioned_product(provisioning_parameters=_params(), availability_zones_triggered=list(tried))
    _provision(deps, product)
    assertpy.assert_that(_saved(mock_provisioned_product_repo).statusReason).is_equal_to(
        "InsufficientCapacityInAllAvailabilityZones"
    )

    product = get_provisioned_product(provisioning_parameters=_params(), availability_zones_triggered=list(tried))
    product.availabilityZonesUnsupported = list(tried)
    _provision(deps, product)
    reason = _saved(mock_provisioned_product_repo).statusReason
    assertpy.assert_that(classify_reason(reason)).is_equal_to(FailureCode.UnsupportedInAz)


def test_fail_launch_retries_another_az_when_ec2_answers_unsupported(
    mock_logger,
    mock_publisher,
    mock_message_bus,
    mock_unit_of_work,
    mock_provisioned_product_repo,
    mock_provisioned_products_qs,
    get_provisioned_product,
    mock_products_srv,
):
    mock_provisioned_products_qs.get_by_id.return_value = get_provisioned_product(
        status=product_status.ProductStatus.Provisioning,
        provisioning_parameters=_params(),
        availability_zones_triggered=["az-3"],
        user_ip_address="127.0.0.1",
    )
    mock_products_srv.has_provisioned_product_insufficient_capacity_error.return_value = False
    mock_products_srv.has_provisioned_product_unsupported_instance_type_error.return_value = True

    fail_launch.handle(
        command=fail_product_launch_command.FailProductLaunchCommand(
            provisioned_product_id=provisioned_product_id_value_object.from_str("pp-123")
        ),
        publisher=mock_publisher,
        logger=mock_logger,
        virtual_targets_qs=mock_provisioned_products_qs,
        products_srv=mock_products_srv,
    )

    published = mock_message_bus.publish.call_args.args[0]
    assertpy.assert_that(published).is_instance_of(insufficient_capacity_reached.InsufficientCapacityReached)
    assertpy.assert_that(_saved(mock_provisioned_product_repo).availabilityZonesUnsupported).is_equal_to(["az-3"])
