from datetime import datetime, timezone
from uuid import uuid4

from app.projects.domain.command_handlers.project_accounts import account_onboarding_publisher
from app.projects.domain.commands.project_accounts import reonboard_project_account_command
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_account
from app.projects.domain.ports import projects_query_service
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def handle(
    command: reonboard_project_account_command.ReonboardProjectAccountCommand,
    unit_of_work: unit_of_work.UnitOfWork,
    projects_query_service: projects_query_service.ProjectsQueryService,
    message_bus: message_bus.MessageBus,
    web_application_account_id: str,
    web_application_environment: str,
    web_application_region: str,
    image_service_account_id: str,
    catalog_service_account_id: str,
):
    project = projects_query_service.get_project_by_id(command.project_id.value)
    if not project:
        raise domain_exception.DomainException("Provided project does not exist.")

    with unit_of_work:
        account = unit_of_work.get_repository(
            project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
        ).get(
            project_account.ProjectAccountPrimaryKey(
                projectId=command.project_id.value,
                id=command.account_id.value,
            )
        )

    if not account:
        raise domain_exception.DomainException("Account does not exist.")

    account_repo = unit_of_work.get_repository(project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount)
    if account.accountStatus in (
        project_account.ProjectAccountStatusEnum.OnBoarding,
        project_account.ProjectAccountStatusEnum.ReOnboarding,
    ):
        if account.onboardingPublicationStatus == project_account.ProjectAccountOnboardingPublicationStatus.Published:
            return
        if not account.onboardingOperationId:
            account.onboardingOperationId = str(uuid4())
            account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Pending
            with unit_of_work:
                account_repo.update_entity(
                    project_account.ProjectAccountPrimaryKey(projectId=command.project_id.value, id=account.id),
                    account,
                )
                unit_of_work.commit()
        account_onboarding_publisher.publish_restart(
            account,
            project,
            message_bus,
            web_application_account_id,
            web_application_environment,
            web_application_region,
            image_service_account_id,
            catalog_service_account_id,
        )
        account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Published
        with unit_of_work:
            account_repo.update_entity(
                project_account.ProjectAccountPrimaryKey(projectId=command.project_id.value, id=account.id),
                account,
            )
            unit_of_work.commit()
        return

    current_time = datetime.now(timezone.utc).isoformat()

    account.lastUpdateDate = current_time
    account.onboardingOperationId = str(uuid4())
    account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Pending
    account.accountStatus = (
        project_account.ProjectAccountStatusEnum.OnBoarding
        if account.accountStatus != project_account.ProjectAccountStatusEnum.Active
        else project_account.ProjectAccountStatusEnum.ReOnboarding
    )

    with unit_of_work:
        account_repo.update_entity(
            project_account.ProjectAccountPrimaryKey(
                projectId=command.project_id.value,
                id=command.account_id.value,
            ),
            account,
        )
        unit_of_work.commit()

    account_onboarding_publisher.publish_restart(
        account,
        project,
        message_bus,
        web_application_account_id,
        web_application_environment,
        web_application_region,
        image_service_account_id,
        catalog_service_account_id,
    )
    account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Published
    with unit_of_work:
        account_repo.update_entity(
            project_account.ProjectAccountPrimaryKey(projectId=command.project_id.value, id=account.id),
            account,
        )
        unit_of_work.commit()
