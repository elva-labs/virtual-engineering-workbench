import aws_cdk

from app.shared.middleware import read_only_clients
from infra import config
from infra.backend import s2s_read_only_clients


def app_config(environment_config: dict) -> config.AppConfig:
    return config.AppConfig(
        account="111111111111",
        region="eu-west-1",
        environment="dev",
        web_app_account="111111111111",
        image_service_account="111111111111",
        catalog_service_account="111111111111",
        component_name="projects",
        environment_config=environment_config,
        component_specific={},
    )


def test_off_by_default_no_environment_variable():
    stack = aws_cdk.Stack(aws_cdk.App(), "Test")
    for environment_config in ({}, {s2s_read_only_clients.CONFIG_KEY: []}):
        assert s2s_read_only_clients.environment(stack, app_config(environment_config)) == {}


def test_configured_clients_are_resolved_from_their_parameters_at_deployment():
    stack = aws_cdk.Stack(aws_cdk.App(), "Test")
    environment = s2s_read_only_clients.environment(
        stack, app_config({s2s_read_only_clients.CONFIG_KEY: ["/x/reader-a", "/x/reader-b"]})
    )
    assert list(environment) == [read_only_clients.ENVIRONMENT_VARIABLE]
    resolved = stack.resolve(environment[read_only_clients.ENVIRONMENT_VARIABLE])
    # Fn::Join of the two CloudFormation SSM parameter values, never a literal id.
    assert resolved["Fn::Join"][0] == ","
    assert len(resolved["Fn::Join"][1]) == 2
    assert all("Ref" in item for item in resolved["Fn::Join"][1])


def test_every_environment_has_none_by_default():
    for environment in config.env_config.values():
        assert environment[s2s_read_only_clients.CONFIG_KEY] == []
