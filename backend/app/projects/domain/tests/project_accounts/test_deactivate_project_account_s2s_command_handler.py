import datetime
import uuid

import assertpy
import pytest

from app.projects.domain.command_handlers.project_accounts import (
    deactivate_project_account_s2s_command_handler as command_handler,
)
from app.projects.domain.commands.project_accounts import deactivate_project_account_s2s_command
from app.projects.domain.exceptions.domain_exception import ProjectAccountStateConflict
from app.projects.domain.model import project_account
from app.projects.domain.value_objects import account_id_value_object, project_id_value_object


def _account(status):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return project_account.ProjectAccount(
        id=str(uuid.uuid4()),
        awsAccountId="123456789012",
        accountType="USER",
        accountName="Test",
        accountDescription="Test account",
        createDate=now,
        lastUpdateDate=now,
        accountStatus=status,
        technologyId="tech-123",
        stage="dev",
        region="us-east-1",
        projectId="project-123",
    )


def _command(account_id):
    return deactivate_project_account_s2s_command.DeactivateProjectAccountS2SCommand(
        project_id=project_id_value_object.from_str("project-123"),
        account_id=account_id_value_object.from_str(account_id),
    )


def test_deactivation_marks_active_account_inactive_without_publish_or_delete(
    sample_project, handler_dependencies, mock_uow_2, mock_account_repo
):
    account = _account(project_account.ProjectAccountStatusEnum.Active)
    _, projects_qs, message_bus_mock = handler_dependencies
    projects_qs.get_project_by_id.return_value = sample_project
    projects_qs.get_project_account_by_id.return_value = account

    command_handler.handle(
        command=_command(account.id),
        unit_of_work=mock_uow_2,
        projects_query_service=projects_qs,
    )

    mock_uow_2.commit.assert_called_once()
    key, updated = mock_account_repo.update_entity.call_args.args
    assertpy.assert_that(key).is_equal_to(
        project_account.ProjectAccountPrimaryKey(projectId="project-123", id=account.id)
    )
    assertpy.assert_that(updated.accountStatus).is_equal_to(project_account.ProjectAccountStatusEnum.Inactive)
    assertpy.assert_that(updated.lastUpdateDate).is_not_empty()
    mock_account_repo.remove.assert_not_called()
    message_bus_mock.publish.assert_not_called()


def test_repeated_deactivation_of_inactive_account_is_a_noop(
    sample_project, handler_dependencies, mock_uow_2, mock_account_repo
):
    account = _account(project_account.ProjectAccountStatusEnum.Inactive)
    _, projects_qs, message_bus_mock = handler_dependencies
    projects_qs.get_project_by_id.return_value = sample_project
    projects_qs.get_project_account_by_id.return_value = account

    command_handler.handle(
        command=_command(account.id),
        unit_of_work=mock_uow_2,
        projects_query_service=projects_qs,
    )

    mock_uow_2.commit.assert_not_called()
    mock_account_repo.update_entity.assert_not_called()
    mock_account_repo.remove.assert_not_called()
    message_bus_mock.publish.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [
        project_account.ProjectAccountStatusEnum.Creating,
        project_account.ProjectAccountStatusEnum.OnBoarding,
        project_account.ProjectAccountStatusEnum.ReOnboarding,
        project_account.ProjectAccountStatusEnum.OffBoarding,
        project_account.ProjectAccountStatusEnum.Archived,
        project_account.ProjectAccountStatusEnum.Failed,
    ],
)
def test_unsupported_status_returns_typed_conflict_without_mutation(
    sample_project, handler_dependencies, mock_uow_2, mock_account_repo, status
):
    account = _account(status)
    _, projects_qs, message_bus_mock = handler_dependencies
    projects_qs.get_project_by_id.return_value = sample_project
    projects_qs.get_project_account_by_id.return_value = account

    with pytest.raises(ProjectAccountStateConflict):
        command_handler.handle(
            command=_command(account.id),
            unit_of_work=mock_uow_2,
            projects_query_service=projects_qs,
        )

    mock_uow_2.commit.assert_not_called()
    mock_account_repo.update_entity.assert_not_called()
    mock_account_repo.remove.assert_not_called()
    message_bus_mock.publish.assert_not_called()


def test_absent_account_is_idempotent_for_delete(sample_project, handler_dependencies, mock_uow_2, mock_account_repo):
    _, projects_qs, message_bus_mock = handler_dependencies
    projects_qs.get_project_by_id.return_value = sample_project
    projects_qs.get_project_account_by_id.return_value = None

    command_handler.handle(
        command=_command("absent-account"),
        unit_of_work=mock_uow_2,
        projects_query_service=projects_qs,
    )

    mock_uow_2.commit.assert_not_called()
    mock_account_repo.update_entity.assert_not_called()
    mock_account_repo.remove.assert_not_called()
    message_bus_mock.publish.assert_not_called()
