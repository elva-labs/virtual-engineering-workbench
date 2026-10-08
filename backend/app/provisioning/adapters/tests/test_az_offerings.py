"""the AZ offerings lookup (cached, fail-open) and the EC2 Unsupported detection."""

from unittest import mock

import assertpy
import pytest
from botocore.exceptions import ClientError

from app.provisioning.adapters.services import ec2_instance_management_service, sc_products_service


@pytest.fixture(autouse=True)
def _empty_cache():
    ec2_instance_management_service._offerings_cache.clear()
    yield
    ec2_instance_management_service._offerings_cache.clear()


def _service(pages=None, error=None):
    ec2 = mock.MagicMock()
    paginator = ec2.get_paginator.return_value
    if error:
        paginator.paginate.side_effect = error
    else:
        paginator.paginate.return_value = pages or []
    provider = mock.MagicMock(return_value=ec2)
    return ec2_instance_management_service.EC2InstanceManagementService(ec2_boto_client_provider=provider), ec2


def _offered(service, instance_type="g6.xlarge", account="001234567890"):
    return service.get_offered_availability_zones(
        user_id="T0011AA", aws_account_id=account, region="eu-north-1", instance_type=instance_type
    )


def test_offered_zones_are_read_from_ec2_and_cached():
    service, ec2 = _service(
        pages=[
            {"InstanceTypeOfferings": [{"InstanceType": "g6.xlarge", "Location": "eu-north-1a"}]},
            {"InstanceTypeOfferings": [{"InstanceType": "g6.xlarge", "Location": "eu-north-1b"}]},
        ]
    )

    assertpy.assert_that(_offered(service)).is_equal_to({"eu-north-1a", "eu-north-1b"})
    assertpy.assert_that(_offered(service)).is_equal_to({"eu-north-1a", "eu-north-1b"})

    ec2.get_paginator.assert_called_once_with("describe_instance_type_offerings")
    ec2.get_paginator.return_value.paginate.assert_called_once_with(
        LocationType="availability-zone", Filters=[{"Name": "instance-type", "Values": ["g6.xlarge"]}]
    )


def test_the_cache_is_per_account_and_instance_type():
    service, ec2 = _service(pages=[{"InstanceTypeOfferings": [{"Location": "eu-north-1a"}]}])

    _offered(service)
    _offered(service, instance_type="m7i.xlarge")
    _offered(service, account="111111111111")

    assertpy.assert_that(ec2.get_paginator.return_value.paginate.call_count).is_equal_to(3)


def test_the_cache_expires(monkeypatch):
    service, ec2 = _service(pages=[{"InstanceTypeOfferings": [{"Location": "eu-north-1a"}]}])
    now = [1000.0]
    monkeypatch.setattr(ec2_instance_management_service.time, "monotonic", lambda: now[0])

    _offered(service)
    now[0] += ec2_instance_management_service.OFFERINGS_TTL_SECONDS + 1
    _offered(service)

    assertpy.assert_that(ec2.get_paginator.return_value.paginate.call_count).is_equal_to(2)


def test_offerings_fail_open_when_ec2_refuses():
    denied = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, "DescribeInstanceTypeOfferings")
    service, _ = _service(error=denied)

    assertpy.assert_that(_offered(service)).is_none()
    assertpy.assert_that(ec2_instance_management_service._offerings_cache).is_empty()


UNSUPPORTED = (
    'Resource handler returned message: "Your requested instance type (g6.xlarge) is not supported in your '
    "requested Availability Zone (eu-north-1c). Please retry your request by not specifying an Availability "
    'Zone or choosing eu-north-1a, eu-north-1b. (Service: Ec2, Status Code: 400, Request ID: 1)"'
)


@pytest.mark.parametrize(
    "reason,instance_type,expected",
    [
        (UNSUPPORTED, "g6.xlarge", True),
        (UNSUPPORTED, "m7i.xlarge", False),
        (
            "We currently do not have sufficient g6.xlarge capacity in the Availability Zone you requested",
            "g6.xlarge",
            False,
        ),
        (None, "g6.xlarge", False),
    ],
)
def test_unsupported_instance_type_is_detected_from_the_stack_events(reason, instance_type, expected):
    service = sc_products_service.ServiceCatalogProductsService(
        sc_boto_client_provider=mock.MagicMock(), cf_boto_client_provider=mock.MagicMock(), logger=mock.MagicMock()
    )
    service._ServiceCatalogProductsService__get_stack_events = mock.MagicMock(
        return_value=[{"ResourceStatusReason": "Resource creation cancelled"}, {"ResourceStatusReason": reason}]
    )

    found = service.has_provisioned_product_unsupported_instance_type_error(
        provisioned_product_id="pp-123",
        user_id="T0011AA",
        aws_account_id="001234567890",
        region="eu-north-1",
        provisioned_instance_type=instance_type,
    )

    assertpy.assert_that(found).is_equal_to(expected)
