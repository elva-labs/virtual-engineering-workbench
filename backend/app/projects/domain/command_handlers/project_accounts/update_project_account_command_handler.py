from datetime import datetime, timezone
from uuid import uuid4

from app.projects.domain.command_handlers.project_accounts import account_onboarding_publisher
from app.projects.domain.commands.project_accounts import update_project_account_command
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_account
from app.projects.domain.ports import projects_query_service, technologies_query_service
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def handle(
    command: update_project_account_command.UpdateProjectAccountCommand,
    unit_of_work: unit_of_work.UnitOfWork,
    projects_query_service: projects_query_service.ProjectsQueryService,
    technologies_query_service: technologies_query_service.TechnologiesQueryService,
    message_bus: message_bus.MessageBus,
    web_application_account_id: str,
    web_application_environment: str,
    web_application_region: str,
    image_service_account_id: str,
    catalog_service_account_id: str,
) -> None:
    project_id = command.project_id.value
    desired_technology_id = command.technology.value
    project = _get_project_with_technology(
        project_id,
        desired_technology_id,
        projects_query_service,
        technologies_query_service,
    )

    account_key = project_account.ProjectAccountPrimaryKey(
        projectId=project_id,
        id=command.account_id.value,
    )
    with unit_of_work:
        account = unit_of_work.get_repository(
            project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
        ).get(account_key)
    if not account:
        raise domain_exception.DomainException("Account does not exist.")

    desired_metadata = (
        command.account_name.value,
        command.account_description.value,
    )
    desired_operational = (
        command.account_type.value,
        desired_technology_id,
        command.stage,
        command.region.value,
    )
    current_metadata = (account.accountName, account.accountDescription)
    current_operational = (
        account.accountType,
        account.technologyId,
        account.stage,
        account.region,
    )
    metadata_changed = current_metadata != desired_metadata
    operational_changed = current_operational != desired_operational
    # A new onboarding revision asks for the same configuration to be onboarded again;
    # None (the portal) keeps the stored one. The first revision on an account that has none only
    # records it: adopting the attribute (e.g. importing into Terraform) is not a re-onboarding request.
    revision_sent = (
        command.onboarding_revision is not None and command.onboarding_revision != account.onboardingRevision
    )
    revision_adopted = revision_sent and account.onboardingRevision is None
    metadata_changed = metadata_changed or revision_adopted
    operational_changed = operational_changed or (revision_sent and not revision_adopted)
    if _resume_inflight_onboarding(
        account=account,
        project=project,
        account_key=account_key,
        unit_of_work=unit_of_work,
        message_bus=message_bus,
        metadata_changed=metadata_changed,
        operational_changed=operational_changed,
        web_application_account_id=web_application_account_id,
        web_application_environment=web_application_environment,
        web_application_region=web_application_region,
        image_service_account_id=image_service_account_id,
        catalog_service_account_id=catalog_service_account_id,
    ):
        return

    if account.accountStatus != project_account.ProjectAccountStatusEnum.Active:
        raise domain_exception.DomainException(
            f"Account cannot be updated while its status is {account.accountStatus}."
        )

    if not metadata_changed and not operational_changed:
        if account.lastOnboardingResult == project_account.ProjectAccountOnboardingResult.Succeeded:
            return
        # An identical failed/unknown configuration is a retry request. Give the
        # attempt a fresh operation identity while retaining the account record.
        operational_changed = True

    account.accountName = command.account_name.value
    account.accountDescription = command.account_description.value
    account.accountType = command.account_type.value
    account.technologyId = desired_technology_id
    account.stage = command.stage
    account.region = command.region.value
    if command.onboarding_revision is not None:
        account.onboardingRevision = command.onboarding_revision
    account.lastUpdateDate = datetime.now(timezone.utc).isoformat()

    if not operational_changed:
        _persist(unit_of_work, account_key, account)
        return

    account.accountStatus = project_account.ProjectAccountStatusEnum.ReOnboarding
    account.onboardingOperationId = str(uuid4())
    account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Pending
    _persist(unit_of_work, account_key, account)
    _publish_and_mark(
        account,
        project,
        unit_of_work,
        account_key,
        message_bus,
        web_application_account_id,
        web_application_environment,
        web_application_region,
        image_service_account_id,
        catalog_service_account_id,
    )


def _get_project_with_technology(project_id, technology_id, projects_query_service, technologies_query_service):
    project = projects_query_service.get_project_by_id(project_id)
    if not project:
        raise domain_exception.DomainException("Provided project does not exist.")
    if not technologies_query_service.get_technology_by_id(project_id, technology_id):
        raise domain_exception.DomainException("Provided technology does not exist in this project.")
    return project


def _persist(unit_of_work, account_key, account) -> None:
    with unit_of_work:
        unit_of_work.get_repository(
            project_account.ProjectAccountPrimaryKey, project_account.ProjectAccount
        ).update_entity(account_key, account)
        unit_of_work.commit()


def _resume_inflight_onboarding(
    account,
    project,
    account_key,
    unit_of_work,
    message_bus,
    metadata_changed,
    operational_changed,
    web_application_account_id,
    web_application_environment,
    web_application_region,
    image_service_account_id,
    catalog_service_account_id,
) -> bool:
    if account.accountStatus not in (
        project_account.ProjectAccountStatusEnum.OnBoarding,
        project_account.ProjectAccountStatusEnum.ReOnboarding,
    ):
        return False
    if metadata_changed or operational_changed:
        raise domain_exception.DomainException("Account configuration cannot change while onboarding is in progress.")
    if account.onboardingPublicationStatus == project_account.ProjectAccountOnboardingPublicationStatus.Published:
        return True

    operation_id_missing = not account.onboardingOperationId
    if operation_id_missing:
        account.onboardingOperationId = str(uuid4())
    if (
        operation_id_missing
        or account.onboardingPublicationStatus != project_account.ProjectAccountOnboardingPublicationStatus.Pending
    ):
        account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Pending
        _persist(unit_of_work, account_key, account)
    _publish_and_mark(
        account,
        project,
        unit_of_work,
        account_key,
        message_bus,
        web_application_account_id,
        web_application_environment,
        web_application_region,
        image_service_account_id,
        catalog_service_account_id,
    )
    return True


def _publish_and_mark(
    account,
    project,
    unit_of_work,
    account_key,
    message_bus,
    web_application_account_id,
    web_application_environment,
    web_application_region,
    image_service_account_id,
    catalog_service_account_id,
) -> None:
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
    _persist(unit_of_work, account_key, account)
