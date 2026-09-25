import json

import aws_cdk
from aws_cdk.assertions import Template

from infra import config, constants
from infra.backend import packaging_app_stack
from infra.backend.integration_oauth_stack import IntegrationOauthStack


def test_packaging_s2s_path_is_stable():
    assert constants.CUSTOM_DNS_S2S_API_PATH_PACKAGING == "clients/packaging/v1"


def test_packaging_stack_has_no_reconciliation_entrypoint():
    assert packaging_app_stack.Entrypoint.S2S_API.value == "s2s-api"
    assert "s2s-reconciliation-events" not in {entry.value for entry in packaging_app_stack.Entrypoint}


def packaging_oauth_template() -> Template:
    app = aws_cdk.App()
    app_config = config.AppConfig(
        account="111111111111",
        region="eu-west-1",
        environment="dev",
        web_app_account="111111111111",
        image_service_account="222222222222",
        catalog_service_account="333333333333",
        component_name="packaging",
        environment_config=config.env_config["dev"],
        component_specific=config.packaging_app_config["dev"],
    )
    stack = IntegrationOauthStack(
        app,
        "RecipeOAuthTest",
        app_config=app_config,
        env=aws_cdk.Environment(account="111111111111", region="eu-west-1"),
    )
    return Template.from_stack(stack)


def test_packaging_resource_server_exposes_all_supported_scopes():
    template = packaging_oauth_template()
    servers = template.find_resources("AWS::Cognito::UserPoolResourceServer")
    packaging = next(
        server["Properties"] for server in servers.values() if server["Properties"]["Identifier"] == "clients/packaging"
    )
    scopes = {scope["ScopeName"] for scope in packaging["Scopes"]}
    assert scopes == {
        "component.read",
        "component.write",
        "component.release",
        "recipe.read",
        "recipe.write",
        "recipe.release",
        "pipeline.read",
        "pipeline.write",
        "pipeline.execute",
    }
    assert "operation.read" not in scopes


def test_sample_s2s_client_is_granted_all_packaging_scopes():
    template = packaging_oauth_template()
    clients = template.find_resources("AWS::Cognito::UserPoolClient")
    assert len(clients) == 1

    allowed_scopes = json.dumps(next(iter(clients.values()))["Properties"].get("AllowedOAuthScopes", []))
    for scope in (
        "component.read",
        "component.write",
        "component.release",
        "recipe.read",
        "recipe.write",
        "recipe.release",
        "pipeline.read",
        "pipeline.write",
        "pipeline.execute",
    ):
        assert scope in allowed_scopes
    assert "operation.read" not in allowed_scopes
