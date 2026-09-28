from datetime import datetime, timezone

from app.projects.domain.command_handlers.internal import string_utils
from app.projects.domain.commands.project_accounts import fail_project_account_onboarding_command
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_account
from app.projects.domain.ports import projects_query_service
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def handle(
    command: fail_project_account_onboarding_command.FailProjectAccountOnboarding,
    projects_qs: projects_query_service.ProjectsQueryService,
    uow: unit_of_work.UnitOfWork,
):
    if not projects_qs.get_project_by_id(id=command.project_id.value):
        raise domain_exception.DomainException(f"Project {command.project_id.value} does not exist")

    if not (
        account := projects_qs.get_project_account_by_id(
            project_id=command.project_id.value, account_id=command.account_id.value
        )
    ):
        raise domain_exception.DomainException(f"Account {command.account_id.value} does not exist")

    if account.accountStatus not in [
        project_account.ProjectAccountStatusEnum.OnBoarding,
        project_account.ProjectAccountStatusEnum.ReOnboarding,
    ]:
        raise domain_exception.DomainException(f"Account {command.account_id.value} is not onboarding")

    account.lastOnboardingResult = project_account.ProjectAccountOnboardingResult.Failed
    account.lastUpdateDate = datetime.now(timezone.utc).isoformat()

    if account.accountStatus == project_account.ProjectAccountStatusEnum.OnBoarding:
        account.accountStatus = project_account.ProjectAccountStatusEnum.Failed
    elif account.accountStatus == project_account.ProjectAccountStatusEnum.ReOnboarding:
        account.accountStatus = project_account.ProjectAccountStatusEnum.Active

    account.lastOnboardingErrorMessage = string_utils.safe_onboarding_error(
        error=command.error.error,
        cause=command.error.cause,
    )

    with uow:
        uow.get_repository(project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount).update_entity(
            project_account.ProjectAccountPrimaryKey(projectId=command.project_id.value, id=command.account_id.value),
            account,
        )
        uow.commit()
