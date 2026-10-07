from __future__ import annotations

from typing import TYPE_CHECKING, Any

import boto3
import botocore.exceptions
from mypy_boto3_ec2 import client

from app.publishing.adapters.exceptions import adapter_exception
from app.publishing.domain.ports import image_service
from app.shared.api import sts_api

if TYPE_CHECKING:
    from mypy_boto3_kms import client as kms_client

SESSION_USER = "ProductPublishingProcess"


class EC2ImageService(image_service.ImageService):
    def __init__(
        self,
        image_srv_role: str,
        image_srv_aws_account_id: str,
        image_srv_key_name: str,
        image_srv_region: str,
        image_import_role: str = "",
        image_import_bucket_prefix: str = "",
        store_with_own_credentials: bool = False,
        boto_session: Any = None,
    ):
        self._image_srv_role = image_srv_role
        self._image_srv_aws_account_id = image_srv_aws_account_id
        self._image_srv_key_name = image_srv_key_name
        self._image_srv_region = image_srv_region
        # "store-restore" distribution: the role that restores images in each target account and
        # the prefix of the target accounts' import buckets (<prefix>-<account>-<region>).
        self._image_import_role = image_import_role
        self._image_import_bucket_prefix = image_import_bucket_prefix
        # When the image service account is this Lambda's own account, the store task runs with the
        # Lambda's own role (useful where only that role may write into target accounts).
        self._store_with_own_credentials = store_with_own_credentials
        self._boto_session = boto_session

    def _create_ec2_client(
        self, region: str, access_key_id: str, secret_access_key: str, session_token: str
    ) -> client.EC2Client:
        session = self._boto_session or boto3
        return session.client(
            "ec2",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
        )

    def _create_kms_client(
        self, region: str, access_key_id: str, secret_access_key: str, session_token: str
    ) -> kms_client.KMSClient:
        session = self._boto_session or boto3
        return session.client(
            "kms",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
        )

    def copy_ami(self, region: str, original_ami_id: str, ami_name: str, ami_description: str) -> str:
        with sts_api.STSAPI(
            self._image_srv_aws_account_id, region, self._image_srv_role, SESSION_USER, self._boto_session
        ) as sts:
            access_key_id, secret_access_key, session_token = sts.get_temp_creds()
            ec2_client = self._create_ec2_client(region, access_key_id, secret_access_key, session_token)

            response = ec2_client.copy_image(
                Name=ami_name,
                SourceImageId=original_ami_id,
                SourceRegion=self._image_srv_region,
                ClientToken=original_ami_id,
                CopyImageTags=True,
                Description=ami_description,
                KmsKeyId=f"alias/{self._image_srv_key_name}",
                Encrypted=True,
            )

        return response["ImageId"]

    def share_ami(self, region: str, copied_ami_id: str, aws_account_id: str) -> None:
        with sts_api.STSAPI(
            self._image_srv_aws_account_id, region, self._image_srv_role, SESSION_USER, self._boto_session
        ) as sts:
            access_key_id, secret_access_key, session_token = sts.get_temp_creds()
            ec2_client = self._create_ec2_client(region, access_key_id, secret_access_key, session_token)

            ec2_client.modify_image_attribute(
                ImageId=copied_ami_id, LaunchPermission={"Add": [{"UserId": aws_account_id}]}
            )

    def grant_kms_access(self, region: str, ami_id: str, aws_account_id: str) -> None:
        """Grants the spoke account decrypt access to KMS keys used to encrypt the AMI's EBS snapshots."""
        with sts_api.STSAPI(
            self._image_srv_aws_account_id, region, self._image_srv_role, SESSION_USER, self._boto_session
        ) as sts:
            access_key_id, secret_access_key, session_token = sts.get_temp_creds()

            ec2 = self._create_ec2_client(region, access_key_id, secret_access_key, session_token)
            kms = self._create_kms_client(region, access_key_id, secret_access_key, session_token)

            response = ec2.describe_images(ImageIds=[ami_id], Owners=["self"])
            if not response["Images"]:
                raise adapter_exception.AdapterException(f"Image {ami_id} not found.")

            kms_key_ids = set()
            for bdm in response["Images"][0].get("BlockDeviceMappings", []):
                ebs = bdm.get("Ebs", {})
                if ebs.get("Encrypted") and ebs.get("KmsKeyId"):
                    kms_key_ids.add(ebs["KmsKeyId"])

            for key_id in kms_key_ids:
                kms.create_grant(
                    KeyId=key_id,
                    GranteePrincipal=f"arn:aws:iam::{aws_account_id}:root",
                    Operations=[
                        "Decrypt",
                        "DescribeKey",
                        "CreateGrant",
                        "GenerateDataKey",
                        "ReEncryptFrom",
                        "ReEncryptTo",
                    ],
                )

    def get_copied_ami_status(self, copied_ami_id: str, region: str) -> str:
        with sts_api.STSAPI(
            self._image_srv_aws_account_id, region, self._image_srv_role, SESSION_USER, self._boto_session
        ) as sts:
            access_key_id, secret_access_key, session_token = sts.get_temp_creds()
            ec2_client = self._create_ec2_client(region, access_key_id, secret_access_key, session_token)

            response = ec2_client.describe_images(ImageIds=[copied_ami_id], Owners=["self"])

            if not response["Images"]:
                raise adapter_exception.AdapterException(f"Image {copied_ami_id} not found.")

            if len(response["Images"]) > 1:
                raise adapter_exception.AdapterException(f"More than 1 image returned. ({response['Images']})")

        return response["Images"][0]["State"]

    # "store-restore" distribution: the image is moved into the target account instead of shared.
    # The store task runs in the image service account (no image key leaves it); the restore runs in
    # the target account as its image import role.

    def _store_ec2_client(self, region: str) -> client.EC2Client:
        if self._store_with_own_credentials:
            session = self._boto_session or boto3
            return session.client("ec2", region_name=region)
        sts = sts_api.STSAPI(
            self._image_srv_aws_account_id, region, self._image_srv_role, SESSION_USER, self._boto_session
        )
        with sts:
            return self._create_ec2_client(region, *sts.get_temp_creds())

    def import_bucket(self, aws_account_id: str, region: str) -> str:
        return f"{self._image_import_bucket_prefix}-{aws_account_id}-{region}"

    def store_ami(self, region: str, source_ami_id: str, aws_account_id: str) -> str:
        ec2 = self._store_ec2_client(region)
        bucket = self.import_bucket(aws_account_id, region)
        try:
            return ec2.create_store_image_task(ImageId=source_ami_id, Bucket=bucket)["ObjectKey"]
        except botocore.exceptions.ClientError as error:
            # A retry after a started or finished store: the bucket holds one copy per image.
            existing = self._store_task(ec2, source_ami_id, bucket)
            if existing and existing.get("StoreTaskState") in ("InProgress", "Completed"):
                return existing.get("S3objectKey") or f"{source_ami_id}.bin"
            # EC2 runs one store per image at a time: a version going to several accounts at once waits
            # for the other account's store; the state machine retries.
            if "already in progress" in str(error):
                raise adapter_exception.StoreImageTaskBusy(
                    f"Another store of {source_ami_id} runs; {bucket} waits for it."
                ) from error
            # The bucket already holds the image: an earlier attempt stored it, but EC2 lists only an image's
            # newest store task, which can be another account's when one image goes to several accounts.
            if "already exists in the bucket" in str(error):
                return f"{source_ami_id}.bin"
            raise

    def _store_s3_client(self, region: str):
        """An S3 client with the store's credentials (they write the import bucket)."""
        session = self._boto_session or boto3
        if self._store_with_own_credentials:
            return session.client("s3", region_name=region)
        sts = sts_api.STSAPI(
            self._image_srv_aws_account_id, region, self._image_srv_role, SESSION_USER, self._boto_session
        )
        with sts:
            key_id, secret, token = sts.get_temp_creds()
        return session.client(
            "s3", region_name=region, aws_access_key_id=key_id, aws_secret_access_key=secret, aws_session_token=token
        )

    def _object_stored(self, region: str, bucket: str, key: str) -> bool:
        """Whether the import bucket holds the stored image (it appears only when its store completes)."""
        try:
            self._store_s3_client(region).head_object(Bucket=bucket, Key=key)
            return True
        except botocore.exceptions.ClientError:
            return False

    @staticmethod
    def _store_task(ec2: client.EC2Client, source_ami_id: str, bucket: str | None = None) -> dict | None:
        tasks = ec2.describe_store_image_tasks(ImageIds=[source_ami_id]).get("StoreImageTaskResults", [])
        tasks = [t for t in tasks if bucket is None or t.get("Bucket") == bucket]
        return tasks[0] if tasks else None  # newest first

    def get_store_ami_status(self, region: str, source_ami_id: str, aws_account_id: str | None = None) -> str:
        # The task for the target account's bucket: one image can be stored into several accounts at once.
        bucket = self.import_bucket(aws_account_id, region) if aws_account_id else None
        ec2 = self._store_ec2_client(region)
        task = self._store_task(ec2, source_ami_id, bucket)
        if not task and bucket:
            # EC2 lists only an image's newest store task: when the image is stored into several accounts at
            # once, this bucket's task can be hidden by another's. The object tells: a stored image appears
            # only when its store completes.
            if self._object_stored(region, bucket, f"{source_ami_id}.bin"):
                return "Completed"
            if self._store_task(ec2, source_ami_id):
                return "InProgress"
        if not task:
            raise adapter_exception.AdapterException(f"No store task found for image {source_ami_id}.")
        if task.get("StoreTaskState") == "Failed":
            raise adapter_exception.AdapterException(
                f"Storing image {source_ami_id} failed: {task.get('StoreTaskFailureReason', 'unknown')}"
            )
        return task["StoreTaskState"]

    def _target_ec2(self, region: str, aws_account_id: str):
        return sts_api.STSAPI(aws_account_id, region, self._image_import_role, SESSION_USER, self._boto_session)

    def restore_ami(self, region: str, object_key: str, aws_account_id: str, ami_name: str) -> str:
        """Idempotent by name: names are unique per account and region."""
        with self._target_ec2(region, aws_account_id) as sts:
            ec2 = self._create_ec2_client(region, *sts.get_temp_creds())
            existing = ec2.describe_images(Owners=["self"], Filters=[{"Name": "name", "Values": [ami_name]}])
            if existing.get("Images"):
                return existing["Images"][0]["ImageId"]
            tags = [{"Key": "vew:distributedFrom", "Value": object_key}]
            response = ec2.create_restore_image_task(
                Bucket=self.import_bucket(aws_account_id, region),
                ObjectKey=object_key,
                Name=ami_name,
                TagSpecifications=[
                    {"ResourceType": "image", "Tags": tags},
                    {"ResourceType": "snapshot", "Tags": tags},
                ],
            )
            return response["ImageId"]

    def get_distributed_ami_status(self, region: str, ami_id: str, aws_account_id: str) -> str:
        with self._target_ec2(region, aws_account_id) as sts:
            ec2 = self._create_ec2_client(region, *sts.get_temp_creds())
            images = ec2.describe_images(ImageIds=[ami_id], Owners=["self"]).get("Images", [])
        if not images:
            raise adapter_exception.AdapterException(f"Image {ami_id} not found in account {aws_account_id}.")
        image = images[0]
        if image["State"] == "available":
            # Restored snapshots take the target account's EBS default encryption; an unencrypted image
            # usually means that setting is off there, which many landing zones forbid launching.
            unencrypted = [
                bdm.get("DeviceName")
                for bdm in image.get("BlockDeviceMappings", [])
                if "Ebs" in bdm and not bdm["Ebs"].get("Encrypted")
            ]
            if unencrypted:
                raise adapter_exception.AdapterException(
                    f"Image {ami_id} in {aws_account_id} has unencrypted snapshots ({unencrypted}); "
                    "enable EBS encryption by default in that account."
                )
        return image["State"]
