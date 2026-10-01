import logging
from unittest import mock

import assertpy

from app.publishing.domain.command_handlers import restore_ami_command_handler, store_ami_command_handler
from app.publishing.domain.commands import restore_ami_command, store_ami_command
from app.publishing.domain.ports import image_service
from app.publishing.domain.value_objects import ami_id_value_object, aws_account_id_value_object, region_value_object


def test_store_ami_stores_into_the_target_account():
    img_srv = mock.create_autospec(spec=image_service.ImageService)
    img_srv.store_ami.return_value = "ami-54321.bin"

    object_key = store_ami_command_handler.handle(
        cmd=store_ami_command.StoreAmiCommand(
            sourceAmiId=ami_id_value_object.from_str("ami-54321"),
            region=region_value_object.from_str("eu-west-3"),
            awsAccountId=aws_account_id_value_object.from_str("123456789012"),
        ),
        img_srv=img_srv,
        logger=mock.create_autospec(spec=logging.Logger),
    )

    assertpy.assert_that(object_key).is_equal_to("ami-54321.bin")
    img_srv.store_ami.assert_called_once_with(
        region="eu-west-3", source_ami_id="ami-54321", aws_account_id="123456789012"
    )


def test_restore_ami_restores_under_a_stable_name():
    img_srv = mock.create_autospec(spec=image_service.ImageService)
    img_srv.restore_ami.return_value = "ami-target1"

    ami_id = restore_ami_command_handler.handle(
        cmd=restore_ami_command.RestoreAmiCommand(
            originalAmiId=ami_id_value_object.from_str("ami-12345"),
            objectKey="ami-54321.bin",
            region=region_value_object.from_str("eu-west-3"),
            awsAccountId=aws_account_id_value_object.from_str("123456789012"),
        ),
        img_srv=img_srv,
        logger=mock.create_autospec(spec=logging.Logger),
    )

    assertpy.assert_that(ami_id).is_equal_to("ami-target1")
    img_srv.restore_ami.assert_called_once_with(
        region="eu-west-3", object_key="ami-54321.bin", aws_account_id="123456789012", ami_name="vew-ami-12345"
    )
