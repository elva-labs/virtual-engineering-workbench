import logging

from app.publishing.domain.commands import restore_ami_command
from app.publishing.domain.ports import image_service


def distributed_ami_name(original_ami_id: str) -> str:
    """Name of the image in the target account: unique per account and region, stable across retries."""
    return f"vew-{original_ami_id}"


def handle(
    cmd: restore_ami_command.RestoreAmiCommand,
    img_srv: image_service.ImageService,
    logger: logging.Logger,
) -> str:
    """Restores the stored image in the target account, owned by that account and encrypted with its
    default EBS key ("store-restore" image distribution)."""
    ami_id = img_srv.restore_ami(
        region=cmd.region.value,
        object_key=cmd.objectKey,
        aws_account_id=cmd.awsAccountId.value,
        ami_name=distributed_ami_name(cmd.originalAmiId.value),
    )
    logger.debug(f"Restoring {cmd.objectKey} in {cmd.awsAccountId.value} as {ami_id}.")
    return ami_id
