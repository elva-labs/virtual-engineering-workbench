from aws_cdk import aws_ssm
from constructs import Construct

from infra import config


def provider_name(scope: Construct, app_config: config.AppConfig) -> str:
    """Resolve the provider created by the OIDC infrastructure only when opted in."""
    if not app_config.environment_config.get("oidc-groups-enabled", False):
        return ""
    return aws_ssm.StringParameter.value_for_string_parameter(
        scope,
        app_config.environment_config["oidc-groups-provider-name-ssm-param"].format(environment=app_config.environment),
    )
