from infra import config


def account_onboarding_environment(
    app_config: config.AppConfig,
    image_service_account_id: str,
    catalog_service_account_id: str,
) -> dict[str, str]:
    return {
        "WEB_APPLICATION_ACCOUNT_ID": app_config.account,
        "WEB_APPLICATION_ENVIRONMENT": app_config.environment,
        "IMAGE_SERVICE_ACCOUNT_ID": image_service_account_id,
        "CATALOG_SERVICE_ACCOUNT_ID": catalog_service_account_id,
    }
