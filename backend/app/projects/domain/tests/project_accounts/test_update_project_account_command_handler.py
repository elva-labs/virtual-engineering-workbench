from unittest import mock

import pytest

from app.projects.domain.command_handlers.project_accounts import (
    update_project_account_command_handler as command_handler,
)
from app.projects.domain.commands.project_accounts import update_project_account_command
from app.projects.domain.events.project_accounts import project_account_on_boarding_restarted
from app.projects.domain.exceptions.domain_exception import DomainException
from app.projects.domain.model import project_account, technology
from app.projects.domain.ports import projects_query_service, technologies_query_service
from app.projects.domain.value_objects import (
    account_description_value_object,
    account_id_value_object,
    account_name_value_object,
    account_technology_id_value_object,
    account_type_value_object,
    project_id_value_object,
    region_value_object,
)
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def _command(**overrides):
    values = {
        "project_id": project_id_value_object.from_str("123"),
        "account_id": account_id_value_object.from_str("account-123"),
        "account_name": account_name_value_object.from_str("Desired account"),
        "account_description": account_description_value_object.from_str("Desired description"),
        "account_type": account_type_value_object.from_str("USER"),
        "technology": account_technology_id_value_object.from_str("tech-new"),
        "stage": project_account.ProjectAccountStageEnum.QA,
        "region": region_value_object.from_str("eu-west-1"),
    }
    values.update(overrides)
    return update_project_account_command.UpdateProjectAccountCommand(**values)


def _account(**overrides):
    values = {
        "id": "account-123",
        "projectId": "123",
        "awsAccountId": "123456789012",
        "accountType": "USER",
        "accountName": "Old account",
        "accountDescription": "Old description",
        "technologyId": "tech-old",
        "stage": "dev",
        "region": "us-east-1",
        "accountStatus": project_account.ProjectAccountStatusEnum.Active,
        "lastOnboardingResult": project_account.ProjectAccountOnboardingResult.Succeeded,
        "onboardingOperationId": "operation-old",
        "onboardingPublicationStatus": project_account.ProjectAccountOnboardingPublicationStatus.Published,
    }
    values.update(overrides)
    return project_account.ProjectAccount(**values)


def _dependencies(account, sample_project):
    uow = mock.create_autospec(unit_of_work.UnitOfWork, instance=True)
    account_repo = mock.create_autospec(unit_of_work.GenericRepository, instance=True)
    account_repo.get.return_value = account
    uow.get_repository.return_value = account_repo
    projects_qs = mock.create_autospec(projects_query_service.ProjectsQueryService, instance=True)
    projects_qs.get_project_by_id.return_value = sample_project
    technologies_qs = mock.create_autospec(technologies_query_service.TechnologiesQueryService, instance=True)
    technologies_qs.get_technology_by_id.return_value = technology.Technology(
        id="tech-new", project_id="123", name="Technology"
    )
    bus = mock.create_autospec(message_bus.MessageBus, instance=True)
    return uow, account_repo, projects_qs, technologies_qs, bus


def _handle(command, dependencies):
    uow, _, projects_qs, technologies_qs, bus = dependencies
    command_handler.handle(
        command=command,
        unit_of_work=uow,
        projects_query_service=projects_qs,
        technologies_query_service=technologies_qs,
        message_bus=bus,
        web_application_account_id="111111111111",
        web_application_environment="dev",
        web_application_region="eu-west-1",
        image_service_account_id="222222222222",
        catalog_service_account_id="333333333333",
    )


def test_metadata_only_update_persists_without_reonboarding(sample_project):
    account = _account(
        technologyId="tech-new",
        stage=project_account.ProjectAccountStageEnum.QA,
        region="eu-west-1",
    )
    command = _command(
        account_type=account_type_value_object.from_str("USER"),
        technology=account_technology_id_value_object.from_str("tech-new"),
    )
    dependencies = _dependencies(account, sample_project)

    _handle(command, dependencies)

    uow, account_repo, _, technologies_qs, bus = dependencies
    assert account.accountName == "Desired account"
    assert account.accountDescription == "Desired description"
    assert account.accountStatus == project_account.ProjectAccountStatusEnum.Active
    assert account.onboardingOperationId == "operation-old"
    account_repo.update_entity.assert_called_once()
    uow.commit.assert_called_once()
    bus.publish.assert_not_called()
    technologies_qs.get_technology_by_id.assert_called_once_with("123", "tech-new")


def test_operational_update_persists_and_publishes_new_stable_operation(sample_project):
    account = _account()
    dependencies = _dependencies(account, sample_project)
    persisted_snapshots = []
    dependencies[1].update_entity.side_effect = lambda _key, entity: persisted_snapshots.append(entity.model_copy())

    _handle(_command(), dependencies)

    uow, account_repo, _, _, bus = dependencies
    assert account.accountStatus == project_account.ProjectAccountStatusEnum.ReOnboarding
    assert account.onboardingOperationId != "operation-old"
    assert account.onboardingPublicationStatus == project_account.ProjectAccountOnboardingPublicationStatus.Published
    assert len(account_repo.update_entity.call_args_list) == 2
    pending_account = persisted_snapshots[0]
    assert pending_account.accountStatus == project_account.ProjectAccountStatusEnum.ReOnboarding
    assert (
        pending_account.onboardingPublicationStatus == project_account.ProjectAccountOnboardingPublicationStatus.Pending
    )
    event = bus.publish.call_args.args[0]
    assert isinstance(event, project_account_on_boarding_restarted.ProjectAccountOnBoardingRestarted)
    assert event.program_account_id == "account-123"
    assert event.onboarding_operation_id == pending_account.onboardingOperationId
    assert event.account_type == "workbench-user"
    assert event.region == "eu-west-1"
    assert uow.commit.call_count == 2


def test_identical_successful_update_is_a_noop(sample_project):
    command = _command(
        account_name=account_name_value_object.from_str("Old account"),
        account_description=account_description_value_object.from_str("Old description"),
        account_type=account_type_value_object.from_str("USER"),
        technology=account_technology_id_value_object.from_str("tech-old"),
        stage=project_account.ProjectAccountStageEnum.DEV,
        region=region_value_object.from_str("us-east-1"),
    )
    dependencies = _dependencies(_account(), sample_project)
    dependencies[3].get_technology_by_id.return_value = technology.Technology(
        id="tech-old", project_id="123", name="Old technology"
    )

    _handle(command, dependencies)

    uow, account_repo, _, _, bus = dependencies
    account_repo.update_entity.assert_not_called()
    uow.commit.assert_not_called()
    bus.publish.assert_not_called()


def test_pending_identical_update_resumes_same_operation_publication(sample_project):
    account = _account(
        accountName="Desired account",
        accountDescription="Desired description",
        accountType="USER",
        technologyId="tech-new",
        stage="qa",
        region="eu-west-1",
        accountStatus=project_account.ProjectAccountStatusEnum.ReOnboarding,
        onboardingOperationId="pending-operation",
        onboardingPublicationStatus=project_account.ProjectAccountOnboardingPublicationStatus.Pending,
    )
    dependencies = _dependencies(account, sample_project)

    _handle(_command(), dependencies)

    uow, account_repo, _, _, bus = dependencies
    event = bus.publish.call_args.args[0]
    assert event.onboarding_operation_id == "pending-operation"
    assert account.onboardingOperationId == "pending-operation"
    assert account.onboardingPublicationStatus == project_account.ProjectAccountOnboardingPublicationStatus.Published
    account_repo.update_entity.assert_called_once()
    uow.commit.assert_called_once()


def test_pending_update_with_different_desired_values_conflicts(sample_project):
    account = _account(
        accountName="Desired account",
        accountDescription="Desired description",
        accountType="USER",
        technologyId="tech-new",
        stage="qa",
        region="eu-west-1",
        accountStatus=project_account.ProjectAccountStatusEnum.ReOnboarding,
        onboardingOperationId="pending-operation",
        onboardingPublicationStatus=project_account.ProjectAccountOnboardingPublicationStatus.Published,
    )
    dependencies = _dependencies(account, sample_project)
    different = _command(account_name=account_name_value_object.from_str("Changed in flight"))

    with pytest.raises(DomainException, match="cannot change while onboarding"):
        _handle(different, dependencies)

    dependencies[1].update_entity.assert_not_called()
    dependencies[4].publish.assert_not_called()


def test_account_update_requires_technology_in_same_project(sample_project):
    dependencies = _dependencies(_account(), sample_project)
    dependencies[3].get_technology_by_id.return_value = None

    with pytest.raises(DomainException, match="technology does not exist"):
        _handle(_command(), dependencies)

    dependencies[1].update_entity.assert_not_called()
    dependencies[4].publish.assert_not_called()


def test_account_update_rejects_inactive_account(sample_project):
    dependencies = _dependencies(
        _account(accountStatus=project_account.ProjectAccountStatusEnum.Inactive),
        sample_project,
    )

    with pytest.raises(DomainException, match="status is Inactive"):
        _handle(_command(), dependencies)

    dependencies[1].update_entity.assert_not_called()
    dependencies[4].publish.assert_not_called()
