from infra import config
from infra.backend.projects_s2s_runtime_config import account_onboarding_environment


def test_projects_s2s_account_onboarding_environment_is_configured():
    app_config = config.AppConfig(
        account="111111111111",
        region="eu-west-1",
        environment="dev",
        web_app_account="999999999999",
        image_service_account="222222222222",
        catalog_service_account="333333333333",
        component_name="projects",
        environment_config=config.env_config["dev"],
        component_specific=config.projects_app_config["dev"],
    )

    assert account_onboarding_environment(app_config, "222222222222", "333333333333") == {
        "WEB_APPLICATION_ACCOUNT_ID": "111111111111",
        "WEB_APPLICATION_ENVIRONMENT": "dev",
        "IMAGE_SERVICE_ACCOUNT_ID": "222222222222",
        "CATALOG_SERVICE_ACCOUNT_ID": "333333333333",
        "SEVERAL_STAGES_PER_ACCOUNT": "false",
    }
