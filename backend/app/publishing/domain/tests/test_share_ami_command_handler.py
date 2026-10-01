import logging
from unittest import mock

import pytest
from freezegun import freeze_time

from app.publishing.domain.command_handlers import share_ami_command_handler
from app.publishing.domain.commands import share_ami_command
from app.publishing.domain.model import shared_ami
from app.publishing.domain.ports import image_service
from app.publishing.domain.value_objects import ami_id_value_object, aws_account_id_value_object, region_value_object


@pytest.fixture()
def command_mock() -> share_ami_command.ShareAmiCommand:
    return share_ami_command.ShareAmiCommand(
        originalAmiId=ami_id_value_object.from_str("ami-12345"),
        copiedAmiId=ami_id_value_object.from_str("ami-54321"),
        region=region_value_object.from_str("eu-west-3"),
        awsAccountId=aws_account_id_value_object.from_str("123456789012"),
    )


@pytest.fixture
def image_service_mock():
    img_srv_mock = mock.create_autospec(spec=image_service.ImageService)
    img_srv_mock.grant_kms_access.return_value = None
    img_srv_mock.share_ami.return_value = None
    return img_srv_mock


@pytest.fixture()
def logger_mock():
    logger_mock = mock.create_autospec(spec=logging.Logger)
    return logger_mock


@freeze_time("2023-07-28")
def test_copy_ami_command_handler_copies_ami(
    command_mock, mock_unit_of_work, image_service_mock, logger_mock, mock_shared_ami_repo
):
    # ARRANGE

    # ACT
    share_ami_command_handler.handle(
        cmd=command_mock, uow=mock_unit_of_work, img_srv=image_service_mock, logger=logger_mock
    )

    # ASSERT
    image_service_mock.grant_kms_access.assert_called_once_with(
        region="eu-west-3",
        ami_id="ami-54321",
        aws_account_id="123456789012",
    )
    image_service_mock.share_ami.assert_called_once_with(
        region="eu-west-3",
        copied_ami_id="ami-54321",
        aws_account_id="123456789012",
    )
    mock_shared_ami_repo.add.assert_called_once_with(
        shared_ami.SharedAmi(
            originalAmiId="ami-12345",
            copiedAmiId="ami-54321",
            awsAccountId="123456789012",
            region="eu-west-3",
            createDate="2023-07-28T00:00:00+00:00",
            lastUpdateDate="2023-07-28T00:00:00+00:00",
        )
    )
    mock_unit_of_work.commit.assert_called_once()


@freeze_time("2023-07-28")
def test_share_ami_command_handler_only_records_with_store_restore(
    command_mock, mock_unit_of_work, image_service_mock, logger_mock, mock_shared_ami_repo
):
    # ACT: "store-restore" distribution - the image is already the account's own restored copy
    share_ami_command_handler.handle(
        cmd=command_mock,
        uow=mock_unit_of_work,
        img_srv=image_service_mock,
        logger=logger_mock,
        share_with_account=False,
    )

    # ASSERT
    image_service_mock.grant_kms_access.assert_not_called()
    image_service_mock.share_ami.assert_not_called()
    mock_shared_ami_repo.add.assert_called_once()
    mock_unit_of_work.commit.assert_called_once()
