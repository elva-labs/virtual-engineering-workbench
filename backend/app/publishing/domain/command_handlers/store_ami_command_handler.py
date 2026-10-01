import logging

from app.publishing.domain.commands import store_ami_command
from app.publishing.domain.ports import image_service


def handle(
    cmd: store_ami_command.StoreAmiCommand,
    img_srv: image_service.ImageService,
    logger: logging.Logger,
) -> str:
    """Starts moving the image into the target account: stores it in that account's import bucket
    ("store-restore" image distribution)."""
    object_key = img_srv.store_ami(
        region=cmd.region.value, source_ami_id=cmd.sourceAmiId.value, aws_account_id=cmd.awsAccountId.value
    )
    logger.debug(f"Storing {cmd.sourceAmiId.value} for {cmd.awsAccountId.value} as {object_key}.")
    return object_key
