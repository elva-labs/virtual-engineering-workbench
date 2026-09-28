from datetime import datetime, timezone

from app.projects.domain.commands.project_accounts import deactivate_project_account_s2s_command
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_account
from app.projects.domain.ports import projects_query_service
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def handle(
    command: deactivate_project_account_s2s_command.DeactivateProjectAccountS2SCommand,
    unit_of_work: unit_of_work.UnitOfWork,
    projects_query_service: projects_query_service.ProjectsQueryService,
) -> None:
    project_id = command.project_id.value
    account_id = command.account_id.value
    if not projects_query_service.get_project_by_id(project_id):
        raise domain_exception.DomainException("Provided project does not exist.")

    account = projects_query_service.get_project_account_by_id(project_id, account_id)
    if not account or account.accountStatus == project_account.ProjectAccountStatusEnum.Inactive:
        return
    if account.accountStatus != project_account.ProjectAccountStatusEnum.Active:
        raise domain_exception.ProjectAccountStateConflict(
            f"Account cannot be deactivated while its status is {account.accountStatus}."
        )

    account.accountStatus = project_account.ProjectAccountStatusEnum.Inactive
    account.lastUpdateDate = datetime.now(timezone.utc).isoformat()
    account_repo = unit_of_work.get_repository(project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount)
    with unit_of_work:
        account_repo.update_entity(
            project_account.ProjectAccountPrimaryKey(projectId=project_id, id=account_id),
            account,
        )
        unit_of_work.commit()
