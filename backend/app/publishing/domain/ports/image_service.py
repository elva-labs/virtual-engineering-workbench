from abc import ABC, abstractmethod


class ImageService(ABC):
    """Copies images within the image service account and gets them into target accounts.

    Two distribution modes (publishing config "image-distribution"): "share" grants the target
    account the image key and launch permission on the image service account's copy; "store-restore"
    moves the image into the target account instead (store it in the account's import bucket, restore
    it there), for landing zones that deny image sharing or the use of another account's key. The
    restored image is owned by the target account and encrypted with its default EBS key.
    """

    @abstractmethod
    def copy_ami(self, region: str, original_ami_id: str, ami_name: str, ami_description: str) -> str: ...

    @abstractmethod
    def get_copied_ami_status(self, copied_ami_id: str, region: str) -> str: ...

    @abstractmethod
    def grant_kms_access(self, region: str, ami_id: str, aws_account_id: str) -> None: ...

    @abstractmethod
    def share_ami(self, region: str, copied_ami_id: str, aws_account_id: str) -> None: ...

    @abstractmethod
    def store_ami(self, region: str, source_ami_id: str, aws_account_id: str) -> str:
        """Starts storing the image into the target account's import bucket; returns the S3 object key."""

    @abstractmethod
    def get_store_ami_status(self, region: str, source_ami_id: str, aws_account_id: str | None = None) -> str:
        """Store task state: InProgress, Completed or Failed."""

    @abstractmethod
    def restore_ami(self, region: str, object_key: str, aws_account_id: str, ami_name: str) -> str:
        """Restores the stored image in the target account; returns that account's image id."""

    @abstractmethod
    def get_distributed_ami_status(self, region: str, ami_id: str, aws_account_id: str) -> str:
        """State of the restored image in the target account: pending, available or failed."""
