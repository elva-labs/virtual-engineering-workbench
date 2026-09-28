import assertpy
import pytest

from app.projects.domain.command_handlers.project_accounts import (
    on_board_project_account_command_handler as command_handler,
)
from app.projects.domain.events.project_accounts import (
    project_account_on_boarding_started,
)
from app.projects.domain.exceptions.domain_exception import DomainException
from app.projects.domain.model import project_account


def test_on_board_project_account_when_account_is_not_associated_should_publish_event(
    mock_on_board_project_account_command,
    sample_project,
    handler_dependencies,
    mock_uow_2,
):
    # ARRANGE
    cmd = mock_on_board_project_account_command
    _, projects_query_service_mock, message_bus_mock = handler_dependencies

    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.list_project_accounts.return_value = []
    projects_query_service_mock.list_project_accounts_by_aws_account.return_value = []

    # ACT
    command_handler.handle_on_board_project_account_command(
        command=cmd,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
        web_application_account_id="001234567890",
        web_application_environment="dev",
        web_application_region="us-east-1",
        image_service_account_id="001234567890",
        catalog_service_account_id="123456789012",
    )

    # ASSERT
    message_bus_mock.publish.assert_called_once()
    event_obj = message_bus_mock.publish.call_args.args[0]

    assertpy.assert_that(event_obj).is_instance_of(
        project_account_on_boarding_started.ProjectAccountOnBoardingStarted
    )
    event_obj_dict = event_obj.model_dump(by_alias=True)

    assertpy.assert_that(event_obj_dict).contains_entry({"accountId": "001234567890"})
    assertpy.assert_that(event_obj_dict).contains_entry(
        {"accountType": "workbench-user"}
    )
    assertpy.assert_that(event_obj_dict).contains_entry({"accountEnvironment": "dev"})
    assertpy.assert_that(event_obj_dict).contains_entry({"programId": "123"})
    assertpy.assert_that(event_obj_dict).contains_entry({"programName": "Test"})
    assertpy.assert_that(event_obj_dict).contains_entry({"region": "us-east-1"})
    assertpy.assert_that(event_obj_dict.get("onboardingOperationId")).is_not_empty()
    assertpy.assert_that(event_obj_dict).contains_entry(
        {
            "variables": {
                "account": "001234567890",
                "environment": "dev",
                "region": "us-east-1",
                "web-application-account-id": "001234567890",
                "web-application-region": "us-east-1",
                "image-service-account": "001234567890",
                "catalog-service-account": "123456789012",
            }
        }
    )


def test_can_onboard_new_account_to_existing_project(
    mock_on_board_project_account_command,
    sample_project,
    handler_dependencies,
    mock_uow_2,
    mock_account_repo,
):
    # ARRANGE
    cmd = mock_on_board_project_account_command
    _, projects_query_service_mock, message_bus_mock = handler_dependencies

    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.list_project_accounts.return_value = []
    projects_query_service_mock.list_project_accounts_by_aws_account.return_value = []

    # ACT
    command_handler.handle_on_board_project_account_command(
        command=cmd,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
        web_application_account_id="001234567890",
        web_application_environment="dev",
        web_application_region="us-east-1",
        image_service_account_id="001234567890",
        catalog_service_account_id="123456789012",
    )

    # ASSERT
    assertpy.assert_that(mock_uow_2.commit.call_count).is_equal_to(2)
    [project_acct] = mock_account_repo.add.call_args.args

    assertpy.assert_that(project_acct.id).is_not_empty()
    assertpy.assert_that(project_acct.accountName).is_equal_to("Test Account")
    assertpy.assert_that(project_acct.accountDescription).is_equal_to(
        "Test Account Description"
    )
    assertpy.assert_that(project_acct.awsAccountId).is_equal_to("001234567890")
    assertpy.assert_that(project_acct.accountType).is_equal_to("USER")
    assertpy.assert_that(project_acct.accountStatus).is_equal_to("OnBoarding")
    assertpy.assert_that(project_acct.stage).is_equal_to(cmd.stage)
    assertpy.assert_that(project_acct.region).is_equal_to("us-east-1")
    assertpy.assert_that(project_acct.projectId).is_equal_to("123")
    assertpy.assert_that(project_acct.onboardingOperationId).is_not_empty()
    assertpy.assert_that(project_acct.onboardingPublicationStatus).is_equal_to(
        "Published"
    )


def test_reserved_account_id_is_used_for_account_and_onboarding_event(
    mock_on_board_project_account_command,
    sample_project,
    handler_dependencies,
    mock_uow_2,
    mock_account_repo,
):
    command = mock_on_board_project_account_command.model_copy(
        update={"reserved_account_id": "reserved-account-id"}
    )
    _, projects_query_service_mock, message_bus_mock = handler_dependencies
    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.list_project_accounts.return_value = []
    projects_query_service_mock.list_project_accounts_by_aws_account.return_value = []
    mock_account_repo.get.return_value = None

    command_handler.handle_on_board_project_account_command(
        command=command,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
        web_application_account_id="001234567890",
        web_application_environment="dev",
        web_application_region="us-east-1",
        image_service_account_id="001234567890",
        catalog_service_account_id="123456789012",
    )

    account = mock_account_repo.add.call_args.args[0]
    event = message_bus_mock.publish.call_args.args[0]
    assertpy.assert_that(account.id).is_equal_to("reserved-account-id")
    assertpy.assert_that(event.program_account_id).is_equal_to("reserved-account-id")
    assertpy.assert_that(account.onboardingOperationId).is_equal_to(
        event.onboarding_operation_id
    )


def test_reserved_pending_account_recovery_reuses_operation_and_marks_publication(
    mock_on_board_project_account_command,
    sample_project,
    handler_dependencies,
    mock_uow_2,
    mock_account_repo,
):
    command = mock_on_board_project_account_command.model_copy(
        update={"reserved_account_id": "reserved-account-id"}
    )
    _, projects_query_service_mock, message_bus_mock = handler_dependencies
    projects_query_service_mock.get_project_by_id.return_value = sample_project
    account = project_account.ProjectAccount(
        id="reserved-account-id",
        awsAccountId=command.account_id.value,
        accountType=command.account_type.value,
        accountName=command.account_name.value,
        accountDescription=command.account_description.value,
        stage=command.stage,
        technologyId=command.technology.value,
        region=command.region.value,
        projectId=command.project_id.value,
        accountStatus=project_account.ProjectAccountStatusEnum.OnBoarding,
        onboardingOperationId="existing-operation-id",
        onboardingPublicationStatus=project_account.ProjectAccountOnboardingPublicationStatus.Pending,
    )
    mock_account_repo.get.return_value = account

    command_handler.handle_on_board_project_account_command(
        command=command,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
        web_application_account_id="001234567890",
        web_application_environment="dev",
        web_application_region="us-east-1",
        image_service_account_id="001234567890",
        catalog_service_account_id="123456789012",
    )

    event = message_bus_mock.publish.call_args.args[0]
    assertpy.assert_that(event.onboarding_operation_id).is_equal_to(
        "existing-operation-id"
    )
    mock_account_repo.add.assert_not_called()
    assertpy.assert_that(account.onboardingPublicationStatus).is_equal_to(
        project_account.ProjectAccountOnboardingPublicationStatus.Published
    )
    mock_account_repo.update_entity.assert_called_once()


def test_reserved_published_account_replay_does_not_publish_again(
    mock_on_board_project_account_command,
    sample_project,
    handler_dependencies,
    mock_uow_2,
    mock_account_repo,
):
    command = mock_on_board_project_account_command.model_copy(
        update={"reserved_account_id": "reserved-account-id"}
    )
    _, projects_query_service_mock, message_bus_mock = handler_dependencies
    projects_query_service_mock.get_project_by_id.return_value = sample_project
    mock_account_repo.get.return_value = project_account.ProjectAccount(
        id="reserved-account-id",
        awsAccountId=command.account_id.value,
        accountType=command.account_type.value,
        accountName=command.account_name.value,
        accountDescription=command.account_description.value,
        stage=command.stage,
        technologyId=command.technology.value,
        region=command.region.value,
        projectId=command.project_id.value,
        accountStatus=project_account.ProjectAccountStatusEnum.OnBoarding,
        onboardingOperationId="existing-operation-id",
        onboardingPublicationStatus=project_account.ProjectAccountOnboardingPublicationStatus.Published,
    )

    command_handler.handle_on_board_project_account_command(
        command=command,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
        web_application_account_id="001234567890",
        web_application_environment="dev",
        web_application_region="us-east-1",
        image_service_account_id="001234567890",
        catalog_service_account_id="123456789012",
    )

    message_bus_mock.publish.assert_not_called()


def test_reserved_inactive_account_reuses_record_and_starts_new_operation(
    mock_on_board_project_account_command,
    sample_project,
    handler_dependencies,
    mock_uow_2,
    mock_account_repo,
):
    command = mock_on_board_project_account_command.model_copy(
        update={"reserved_account_id": "reserved-account-id"}
    )
    _, projects_query_service_mock, message_bus_mock = handler_dependencies
    projects_query_service_mock.get_project_by_id.return_value = sample_project
    previous_operation_id = "previous-operation-id"
    account = project_account.ProjectAccount(
        id="reserved-account-id",
        awsAccountId=command.account_id.value,
        accountType=command.account_type.value,
        accountName=command.account_name.value,
        accountDescription=command.account_description.value,
        stage=command.stage,
        technologyId=command.technology.value,
        region=command.region.value,
        projectId=command.project_id.value,
        accountStatus=project_account.ProjectAccountStatusEnum.Inactive,
        onboardingOperationId=previous_operation_id,
        onboardingPublicationStatus=project_account.ProjectAccountOnboardingPublicationStatus.Published,
    )
    mock_account_repo.get.return_value = account

    command_handler.handle_on_board_project_account_command(
        command=command,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
        web_application_account_id="001234567890",
        web_application_environment="dev",
        web_application_region="us-east-1",
        image_service_account_id="001234567890",
        catalog_service_account_id="123456789012",
    )

    event = message_bus_mock.publish.call_args.args[0]
    assertpy.assert_that(account.id).is_equal_to("reserved-account-id")
    assertpy.assert_that(account.accountStatus).is_equal_to(
        project_account.ProjectAccountStatusEnum.OnBoarding
    )
    assertpy.assert_that(account.onboardingOperationId).is_not_equal_to(
        previous_operation_id
    )
    assertpy.assert_that(event.onboarding_operation_id).is_equal_to(
        account.onboardingOperationId
    )
    mock_account_repo.add.assert_not_called()
    assertpy.assert_that(mock_account_repo.update_entity.call_count).is_equal_to(2)


def test_can_not_onboard_duplicate_stage_for_project_account_type_and_region(
    mock_on_board_project_account_command,
    sample_project,
    sample_project_account,
    handler_dependencies,
    mock_uow_2,
):
    # ARRANGE
    cmd = mock_on_board_project_account_command
    _, projects_query_service_mock, message_bus_mock = handler_dependencies

    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.list_project_accounts.return_value = [
        sample_project_account
    ]
    projects_query_service_mock.list_project_accounts_by_aws_account.return_value = []

    # ACT
    with pytest.raises(DomainException):
        command_handler.handle_on_board_project_account_command(
            command=cmd,
            unit_of_work=mock_uow_2,
            projects_query_service=projects_query_service_mock,
            message_bus=message_bus_mock,
            web_application_account_id="001234567890",
            web_application_environment="dev",
            web_application_region="us-east-1",
            image_service_account_id="001234567890",
            catalog_service_account_id="123456789012",
        )

    # ASSERT
    mock_uow_2.commit.assert_not_called()
    projects_query_service_mock.list_project_accounts.assert_called_once_with(
        "123",
        account_type="USER",
        stage="dev",
        technology_id="321",
    )


def test_can_onboard_to_another_region(
    on_board_project_account_different_region_command,
    sample_project,
    sample_project_account,
    handler_dependencies,
    mock_uow_2,
):
    # ARRANGE
    cmd = on_board_project_account_different_region_command
    _, projects_query_service_mock, message_bus_mock = handler_dependencies

    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.list_project_accounts.return_value = [
        sample_project_account
    ]
    projects_query_service_mock.list_project_accounts_by_aws_account.return_value = []

    # ACT
    command_handler.handle_on_board_project_account_command(
        command=cmd,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
        web_application_account_id="001234567890",
        web_application_environment="dev",
        web_application_region="us-east-1",
        image_service_account_id="001234567890",
        catalog_service_account_id="123456789012",
    )

    # ASSERT
    assertpy.assert_that(mock_uow_2.commit.call_count).is_equal_to(2)


def test_on_board_project_account_when_project_does_not_exist_should_not_associate(
    mock_on_board_project_account_command, handler_dependencies, mock_uow_2
):
    # ARRANGE
    cmd = mock_on_board_project_account_command
    _, projects_query_service_mock, message_bus_mock = handler_dependencies

    projects_query_service_mock.get_project_by_id.return_value = None
    projects_query_service_mock.list_project_accounts_by_aws_account.return_value = []

    # ACT
    with pytest.raises(DomainException):
        command_handler.handle_on_board_project_account_command(
            command=cmd,
            unit_of_work=mock_uow_2,
            projects_query_service=projects_query_service_mock,
            message_bus=message_bus_mock,
            web_application_account_id="001234567890",
            web_application_environment="dev",
            web_application_region="us-east-1",
            image_service_account_id="001234567890",
            catalog_service_account_id="123456789012",
        )

    # ASSERT
    mock_uow_2.commit.assert_not_called()


def test_on_board_project_account_when_account_on_boarded_should_not_on_board(
    mock_on_board_project_account_command,
    handler_dependencies,
    sample_project,
    sample_project_account,
    mock_uow_2,
):
    # ARRANGE
    cmd = mock_on_board_project_account_command
    _, projects_query_service_mock, message_bus_mock = handler_dependencies

    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.list_project_accounts_by_aws_account.return_value = [
        sample_project_account
    ]
    sample_project_account.awsAccountId = "001234567890"
    # ACT & ASSERT
    with pytest.raises(DomainException) as error:
        command_handler.handle_on_board_project_account_command(
            command=cmd,
            unit_of_work=mock_uow_2,
            projects_query_service=projects_query_service_mock,
            message_bus=message_bus_mock,
            web_application_account_id="001234567890",
            web_application_environment="dev",
            web_application_region="us-east-1",
            image_service_account_id="001234567890",
            catalog_service_account_id="123456789012",
        )
    assertpy.assert_that(str(error.value)).is_equal_to(
        f"Account with id: {sample_project_account.awsAccountId} already onboarded"
    )
