from app.projects.domain.events.project_accounts import project_account_on_boarding_restarted
from app.projects.domain.model import project_account
from app.shared.adapters.message_bus import message_bus

ACCOUNT_TYPES = {
    "USER": "workbench-user",
    "TOOLCHAIN": "workbench-toolchain",
}


def publish_restart(
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
        project_account_on_boarding_restarted.ProjectAccountOnBoardingRestarted(
            programAccountId=account.id,
            accountId=account.awsAccountId,
            accountType=ACCOUNT_TYPES[account.accountType.value],
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
