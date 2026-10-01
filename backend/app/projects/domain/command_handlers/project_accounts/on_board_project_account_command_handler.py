from datetime import datetime, timezone
from uuid import uuid4

from app.projects.domain.commands.project_accounts import on_board_project_account_command
from app.projects.domain.events.project_accounts import project_account_on_boarding_started
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_account
from app.projects.domain.ports import projects_query_service
from app.projects.domain.value_objects import account_type_value_object
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work

ACCOUNT_TYPES = {
    account_type_value_object.AccountTypeEnum.USER: "workbench-user",
    account_type_value_object.AccountTypeEnum.TOOLCHAIN: "workbench-toolchain",
}


def _publish_onboarding(
    account: project_account.ProjectAccount,
    project,
    message_bus: message_bus.MessageBus,
    web_application_account_id: str,
    web_application_environment: str,
    web_application_region: str,
    image_service_account_id: str,
    catalog_service_account_id: str,
) -> None:
    message_bus.publish(
        project_account_on_boarding_started.ProjectAccountOnBoardingStarted(
            programAccountId=account.id,
            accountId=account.awsAccountId,
            accountType=ACCOUNT_TYPES[account.accountType],
            programId=project.projectId,
            programName=project.projectName,
            accountEnvironment=account.stage,
            region=account.region,
            onboardingOperationId=account.onboardingOperationId,
            variables={
                "account": account.awsAccountId,
                "environment": web_application_environment,
                "region": account.region,
                "web-application-account-id": web_application_account_id,
                "web-application-region": web_application_region,
                "image-service-account": image_service_account_id,
                "catalog-service-account": catalog_service_account_id,
            },
        )
    )


def handle_on_board_project_account_command(  # noqa: C901
    command: on_board_project_account_command.OnBoardProjectAccountCommand,
    unit_of_work: unit_of_work.UnitOfWork,
    projects_query_service: projects_query_service.ProjectsQueryService,
    message_bus: message_bus.MessageBus,
    web_application_account_id: str,
    web_application_environment: str,
    web_application_region: str,
    image_service_account_id: str,
    catalog_service_account_id: str,
    several_stages_per_account: bool = False,
):
    project = projects_query_service.get_project_by_id(command.project_id.value)
    if not project:
        raise domain_exception.DomainException("Provided project does not exist.")

    if command.reserved_account_id:
        with unit_of_work:
            existing = unit_of_work.get_repository(
                project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
            ).get(
                project_account.ProjectAccountPrimaryKey(
                    projectId=command.project_id.value,
                    id=command.reserved_account_id,
                )
            )
        if existing:
            if existing.awsAccountId != command.account_id.value:
                raise domain_exception.DomainException(
                    "Reserved account id is already associated with another request."
                )
            is_inactive = existing.accountStatus == project_account.ProjectAccountStatusEnum.Inactive
            same_request = (
                existing.accountType == command.account_type.value
                and existing.accountName == command.account_name.value
                and existing.accountDescription == command.account_description.value
                and existing.stage == command.stage
                and existing.technologyId == command.technology.value
                and existing.region == command.region.value
            )
            if not is_inactive and not same_request:
                raise domain_exception.DomainException(
                    "Reserved account id is already associated with another request."
                )
            if is_inactive:
                current_time = datetime.now(timezone.utc).isoformat()
                existing.accountType = command.account_type.value
                existing.accountName = command.account_name.value
                existing.accountDescription = command.account_description.value
                existing.stage = command.stage
                existing.technologyId = command.technology.value
                existing.region = command.region.value
                existing.accountStatus = project_account.ProjectAccountStatusEnum.OnBoarding
                existing.lastOnboardingResult = None
                existing.lastOnboardingErrorMessage = None
                existing.lastUpdateDate = current_time
                existing.onboardingOperationId = str(uuid4())
                existing.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Pending
                with unit_of_work:
                    unit_of_work.get_repository(
                        project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
                    ).update_entity(
                        project_account.ProjectAccountPrimaryKey(
                            projectId=command.project_id.value,
                            id=existing.id,
                        ),
                        existing,
                    )
                    unit_of_work.commit()
            if (
                existing.onboardingPublicationStatus
                != project_account.ProjectAccountOnboardingPublicationStatus.Published
            ):
                _publish_onboarding(
                    existing,
                    project,
                    message_bus,
                    web_application_account_id,
                    web_application_environment,
                    web_application_region,
                    image_service_account_id,
                    catalog_service_account_id,
                )
                existing.onboardingPublicationStatus = (
                    project_account.ProjectAccountOnboardingPublicationStatus.Published
                )
                with unit_of_work:
                    unit_of_work.get_repository(
                        project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
                    ).update_entity(
                        project_account.ProjectAccountPrimaryKey(
                            projectId=command.project_id.value,
                            id=existing.id,
                        ),
                        existing,
                    )
                    unit_of_work.commit()
            return

    account = projects_query_service.list_project_accounts(
        command.project_id.value,
        account_type=command.account_type.value,
        stage=command.stage,
        technology_id=command.technology.value,
    )
    if next((acc for acc in account if acc.region == command.region.value), None):
        raise domain_exception.DomainException("Provided project already has an account of this type, stage and region")

    current_time = datetime.now(timezone.utc).isoformat()

    project_acct = project_account.ProjectAccount(
        id=command.reserved_account_id or project_account.generate_id(),
        awsAccountId=command.account_id.value,
        accountType=command.account_type.value,
        accountName=command.account_name.value,
        accountDescription=command.account_description.value,
        createDate=current_time,
        lastUpdateDate=current_time,
        accountStatus=project_account.ProjectAccountStatusEnum.OnBoarding,
        stage=command.stage,
        technologyId=command.technology.value,
        region=command.region.value,
        projectId=command.project_id.value,
        onboardingOperationId=str(uuid4()),
        onboardingPublicationStatus=project_account.ProjectAccountOnboardingPublicationStatus.Pending,
        onboardingRevision=command.onboarding_revision,
    )

    # An AWS account belongs to one project. With several stages per account it may hold one record per
    # type, stage, technology and region of that project (checked above).
    existing_records = projects_query_service.list_project_accounts_by_aws_account(project_acct.awsAccountId)
    if any(acc.projectId != project_acct.projectId for acc in existing_records) or (
        existing_records and not several_stages_per_account
    ):
        raise domain_exception.DomainException(f"Account with id: {project_acct.awsAccountId} already onboarded")

    with unit_of_work:
        unit_of_work.get_repository(project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount).add(
            project_acct
        )
        unit_of_work.commit()

    _publish_onboarding(
        project_acct,
        project,
        message_bus,
        web_application_account_id,
        web_application_environment,
        web_application_region,
        image_service_account_id,
        catalog_service_account_id,
    )
    project_acct.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Published
    with unit_of_work:
        unit_of_work.get_repository(
            project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
        ).update_entity(
            project_account.ProjectAccountPrimaryKey(projectId=project_acct.projectId, id=project_acct.id),
            project_acct,
        )
        unit_of_work.commit()
