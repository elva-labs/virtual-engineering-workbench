from typing import Any, Callable

from app.packaging.domain.ports import base_image_release_service

BASE_CHANNEL_TAG = "vew:base-channel"


class EC2ImageTagService(base_image_release_service.ImageTagService):
    """Tags released AMIs in the AMI factory account; the admin role may remove only BASE_CHANNEL_TAG."""

    def __init__(self, client_factory: Callable[[str], Any]):
        self._client_factory = client_factory

    def set_base_channels(self, ami_id: str, channels: set[str]) -> None:
        client = self._client_factory("ec2")
        if channels:
            # "prod", "test" or "prod test": the channels this AMI is the base of right now.
            value = " ".join(sorted(channels))
            client.create_tags(Resources=[ami_id], Tags=[{"Key": BASE_CHANNEL_TAG, "Value": value}])
        else:
            client.delete_tags(Resources=[ami_id], Tags=[{"Key": BASE_CHANNEL_TAG}])
