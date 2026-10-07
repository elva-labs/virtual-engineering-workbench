from datetime import datetime, timezone
from unittest import mock

import assertpy
import botocore.client
import botocore.exceptions
import pytest
from mypy_boto3_ec2 import client

from app.publishing.adapters.exceptions import adapter_exception
from app.publishing.adapters.services import ec2_image_service

IMPORT_BUCKET = "vew-image-import-322234948118-eu-west-3"


def test_copy_ami_copies_ami(mock_ec2: client.EC2Client):
    # ARRANGE
    ec2_img_srv = ec2_image_service.EC2ImageService("ImageSrvRole", "123456789012", "test-key", "us-east-1")
    original_ami_id = mock_ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]

    # ACT
    copied_ami_id = ec2_img_srv.copy_ami("eu-west-3", original_ami_id, "Great AMI", "Great AMI description")

    # ASSERT
    assertpy.assert_that(copied_ami_id).is_not_none()


def test_share_ami_shares_ami(mock_moto_calls):
    # ARRANGE
    ec2_img_srv = ec2_image_service.EC2ImageService("ImageSrvRole", "123456789012", "test-key", "us-east-1")

    # ACT
    ec2_img_srv.share_ami("eu-west-3", "ami-54321", "123456789012")

    # ASSERT
    mock_moto_calls["ModifyImageAttribute"].assert_called_once_with(
        ImageId="ami-54321", LaunchPermission={"Add": [{"UserId": "123456789012"}]}
    )


def test_grant_kms_access_creates_grant_for_encrypted_snapshots(mock_moto_calls):
    # ARRANGE
    ec2_img_srv = ec2_image_service.EC2ImageService("ImageSrvRole", "123456789012", "test-key", "us-east-1")

    # ACT
    ec2_img_srv.grant_kms_access("eu-west-3", "ami-12345", "322234948118")

    # ASSERT
    mock_moto_calls["DescribeImages"].assert_called_once_with(ImageIds=["ami-12345"], Owners=["self"])
    mock_moto_calls["CreateGrant"].assert_called_once_with(
        KeyId="string",
        GranteePrincipal="arn:aws:iam::322234948118:root",
        Operations=["Decrypt", "DescribeKey", "CreateGrant", "GenerateDataKey", "ReEncryptFrom", "ReEncryptTo"],
    )


def test_get_copied_ami_status(mock_ec2: client.EC2Client):
    # ARRANGE
    ec2_img_srv = ec2_image_service.EC2ImageService("ImageSrvRole", "123456789012", "test-key", "us-east-1")
    copied_ami_id = mock_ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]

    # ACT
    copied_ami_status = ec2_img_srv.get_copied_ami_status(copied_ami_id, "eu-west-3")
    # ASSERT
    assertpy.assert_that(copied_ami_status).is_not_none()
    assertpy.assert_that(copied_ami_status).is_equal_to("available")


@pytest.fixture()
def ec2_calls():
    """Intercepts the calls of "store-restore" distribution (moto has no store or restore image tasks)."""
    calls = {
        "AssumeRole": mock.MagicMock(
            return_value={
                "Credentials": {
                    "AccessKeyId": "AKIA",
                    "SecretAccessKey": "secret",
                    "SessionToken": "token",
                    "Expiration": datetime.now(timezone.utc),
                }
            }
        ),
        "CreateStoreImageTask": mock.MagicMock(return_value={"ObjectKey": "ami-54321.bin"}),
        "DescribeStoreImageTasks": mock.MagicMock(
            return_value={
                "StoreImageTaskResults": [
                    {
                        "AmiId": "ami-54321",
                        "Bucket": IMPORT_BUCKET,
                        "S3objectKey": "ami-54321.bin",
                        "StoreTaskState": "InProgress",
                    }
                ]
            }
        ),
        "CreateRestoreImageTask": mock.MagicMock(return_value={"ImageId": "ami-target1"}),
        "DescribeImages": mock.MagicMock(return_value={"Images": []}),
        "HeadObject": mock.MagicMock(
            side_effect=botocore.exceptions.ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
            )
        ),
    }

    def _interceptor(self, operation_name, kwarg):
        if operation_name in calls:
            return calls[operation_name](**kwarg)
        return orig(self, operation_name, kwarg)

    orig = botocore.client.BaseClient._make_api_call
    with mock.patch("botocore.client.BaseClient._make_api_call", new=_interceptor):
        yield calls


def _distribution_service(store_with_own_credentials: bool = False):
    return ec2_image_service.EC2ImageService(
        "ImageSrvRole",
        "123456789012",
        "test-key",
        "us-east-1",
        image_import_role="ProductPublishingImageImportRole",
        image_import_bucket_prefix="vew-image-import",
        store_with_own_credentials=store_with_own_credentials,
    )


@pytest.mark.parametrize("own_credentials", [False, True])
def test_store_ami_stores_into_the_target_import_bucket(ec2_calls, own_credentials):
    object_key = _distribution_service(own_credentials).store_ami("eu-west-3", "ami-54321", "322234948118")

    assertpy.assert_that(object_key).is_equal_to("ami-54321.bin")
    ec2_calls["CreateStoreImageTask"].assert_called_once_with(ImageId="ami-54321", Bucket=IMPORT_BUCKET)
    # Without own credentials the store runs as the image service role of the image service account.
    assertpy.assert_that(ec2_calls["AssumeRole"].called).is_equal_to(not own_credentials)


def test_store_ami_retry_returns_the_existing_task(ec2_calls):
    ec2_calls["CreateStoreImageTask"].side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "InvalidParameter", "Message": "already stored"}}, "CreateStoreImageTask"
    )

    object_key = _distribution_service().store_ami("eu-west-3", "ami-54321", "322234948118")

    assertpy.assert_that(object_key).is_equal_to("ami-54321.bin")


def test_store_ami_waits_while_another_store_of_the_image_runs(ec2_calls):
    # A version stored into two accounts at once: EC2 runs one store per image.
    ec2_calls["CreateStoreImageTask"].side_effect = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "InvalidRequest",
                "Message": "A CreateStoreImageTask is already in progress for the AMI. You can't run multiple "
                "concurrent store image requests for the same AMI.",
            }
        },
        "CreateStoreImageTask",
    )
    ec2_calls["DescribeStoreImageTasks"].return_value = {
        "StoreImageTaskResults": [
            {"AmiId": "ami-54321", "Bucket": "vew-image-import-999999999999-eu-west-3", "StoreTaskState": "InProgress"}
        ]
    }

    assertpy.assert_that(_distribution_service().store_ami).raises(
        adapter_exception.StoreImageTaskBusy
    ).when_called_with("eu-west-3", "ami-54321", "322234948118")


def test_store_ami_status_raises_on_failed_task(ec2_calls):
    ec2_calls["DescribeStoreImageTasks"].return_value = {
        "StoreImageTaskResults": [
            {"AmiId": "ami-54321", "StoreTaskState": "Failed", "StoreTaskFailureReason": "no access"}
        ]
    }

    assertpy.assert_that(_distribution_service().get_store_ami_status).raises(
        adapter_exception.AdapterException
    ).when_called_with("eu-west-3", "ami-54321")


def test_store_ami_status_reads_the_task_of_the_target_accounts_bucket(ec2_calls):
    # One image stored into two accounts at once: each distribution reads its own bucket's task.
    ec2_calls["DescribeStoreImageTasks"].return_value = {
        "StoreImageTaskResults": [
            {"AmiId": "ami-54321", "Bucket": "vew-image-import-999999999999-eu-west-3", "StoreTaskState": "Failed"},
            {"AmiId": "ami-54321", "Bucket": IMPORT_BUCKET, "StoreTaskState": "Completed"},
        ]
    }

    status = _distribution_service().get_store_ami_status("eu-west-3", "ami-54321", "322234948118")

    assertpy.assert_that(status).is_equal_to("Completed")


def test_restore_ami_restores_in_the_target_account(ec2_calls):
    ami_id = _distribution_service().restore_ami("eu-west-3", "ami-54321.bin", "322234948118", "vew-ami-orig")

    assertpy.assert_that(ami_id).is_equal_to("ami-target1")
    kwargs = ec2_calls["CreateRestoreImageTask"].call_args.kwargs
    assertpy.assert_that(kwargs["Bucket"]).is_equal_to(IMPORT_BUCKET)
    assertpy.assert_that(kwargs["ObjectKey"]).is_equal_to("ami-54321.bin")
    assertpy.assert_that(kwargs["Name"]).is_equal_to("vew-ami-orig")
    role_arn = ec2_calls["AssumeRole"].call_args.kwargs["RoleArn"]
    assertpy.assert_that(role_arn).is_equal_to("arn:aws:iam::322234948118:role/ProductPublishingImageImportRole")


def test_restore_ami_is_idempotent_by_name(ec2_calls):
    ec2_calls["DescribeImages"].return_value = {"Images": [{"ImageId": "ami-already"}]}

    ami_id = _distribution_service().restore_ami("eu-west-3", "ami-54321.bin", "322234948118", "vew-ami-orig")

    assertpy.assert_that(ami_id).is_equal_to("ami-already")
    ec2_calls["CreateRestoreImageTask"].assert_not_called()


def test_distributed_ami_status_requires_encrypted_snapshots(ec2_calls):
    ec2_calls["DescribeImages"].return_value = {
        "Images": [
            {
                "ImageId": "ami-target1",
                "State": "available",
                "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {"Encrypted": False}}],
            }
        ]
    }

    assertpy.assert_that(_distribution_service().get_distributed_ami_status).raises(
        adapter_exception.AdapterException
    ).when_called_with("eu-west-3", "ami-target1", "322234948118")


@pytest.mark.parametrize("architecture", ["arm64", "x86_64"])
def test_distributed_ami_status_available_for_any_architecture(ec2_calls, architecture):
    ec2_calls["DescribeImages"].return_value = {
        "Images": [
            {
                "ImageId": "ami-target1",
                "State": "available",
                "Architecture": architecture,
                "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {"Encrypted": True}}],
            }
        ]
    }

    state = _distribution_service().get_distributed_ami_status("eu-west-3", "ami-target1", "322234948118")

    assertpy.assert_that(state).is_equal_to("available")


# One image stored into two accounts within milliseconds: both stores ran, but EC2 lists only an image's
# newest store task, so the other account's task hid this bucket's.
_OTHER_ACCOUNTS_TASK = {
    "StoreImageTaskResults": [
        {
            "AmiId": "ami-54321",
            "Bucket": "vew-image-import-999999999999-eu-west-3",
            "StoreTaskState": "Completed",
        }
    ]
}


def test_store_ami_retry_returns_the_key_when_the_bucket_already_holds_the_image(ec2_calls):
    ec2_calls["CreateStoreImageTask"].side_effect = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "InvalidRequest",
                "Message": "The AMI already exists in the bucket. You can't create multiple copies of an AMI in "
                "the same S3 bucket.",
            }
        },
        "CreateStoreImageTask",
    )
    ec2_calls["DescribeStoreImageTasks"].return_value = _OTHER_ACCOUNTS_TASK

    object_key = _distribution_service().store_ami("eu-west-3", "ami-54321", "322234948118")

    assertpy.assert_that(object_key).is_equal_to("ami-54321.bin")


@pytest.mark.parametrize("own_credentials", [False, True])
def test_store_ami_status_is_completed_when_the_object_is_in_the_bucket_but_the_task_is_hidden(
    ec2_calls, own_credentials
):
    ec2_calls["DescribeStoreImageTasks"].return_value = _OTHER_ACCOUNTS_TASK
    ec2_calls["HeadObject"].side_effect = None
    ec2_calls["HeadObject"].return_value = {"ContentLength": 1}

    status = _distribution_service(own_credentials).get_store_ami_status("eu-west-3", "ami-54321", "322234948118")

    assertpy.assert_that(status).is_equal_to("Completed")
    ec2_calls["HeadObject"].assert_called_once_with(Bucket=IMPORT_BUCKET, Key="ami-54321.bin")


def test_store_ami_status_waits_while_another_accounts_task_hides_this_buckets(ec2_calls):
    ec2_calls["DescribeStoreImageTasks"].return_value = _OTHER_ACCOUNTS_TASK

    status = _distribution_service().get_store_ami_status("eu-west-3", "ami-54321", "322234948118")

    assertpy.assert_that(status).is_equal_to("InProgress")


def test_store_ami_status_raises_when_the_image_has_no_store_at_all(ec2_calls):
    ec2_calls["DescribeStoreImageTasks"].return_value = {"StoreImageTaskResults": []}

    assertpy.assert_that(_distribution_service().get_store_ami_status).raises(
        adapter_exception.AdapterException
    ).when_called_with("eu-west-3", "ami-54321", "322234948118")
