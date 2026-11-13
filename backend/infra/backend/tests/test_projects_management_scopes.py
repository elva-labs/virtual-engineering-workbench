import json

import aws_cdk
from aws_cdk.assertions import Template

from infra import config
from infra.backend.integration_oauth_stack import IntegrationOauthStack


def test_project_management_scopes_and_isolated_bootstrap_grant():
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
        "ProjectsOAuthTest",
        app_config=app_config,
        env=aws_cdk.Environment(account="111111111111", region="eu-west-1"),
    )
    template = Template.from_stack(stack)
    servers = template.find_resources("AWS::Cognito::UserPoolResourceServer")
    projects = next(
        value["Properties"] for value in servers.values() if value["Properties"]["Identifier"] == "clients/projects"
    )
    scopes = {entry["ScopeName"] for entry in projects["Scopes"]}
    assert {
        "program.write",
        "group_assignment.read",
        "group_assignment.write",
        "client_assignment.bootstrap",
    } <= scopes
    clients = template.find_resources("AWS::Cognito::UserPoolClient")
    assert len(clients) == 2
    sample = next(value for value in clients.values() if "sample-s2s" in value["Properties"]["ClientName"])
    management = next(
        value for value in clients.values() if "projects-assignment-management" in value["Properties"]["ClientName"]
    )
    assert all("bootstrap" not in value["Properties"]["ClientName"] for value in clients.values())
    assert all(
        "client_assignment.bootstrap" not in json.dumps(value["Properties"]["AllowedOAuthScopes"])
        for value in clients.values()
    )
    sample_scopes = json.dumps(sample["Properties"]["AllowedOAuthScopes"])
    management_scopes = json.dumps(management["Properties"]["AllowedOAuthScopes"])
    assert "program.write" in sample_scopes and "group_assignment.write" in sample_scopes
    assert "client_assignment.write" not in sample_scopes
    assert "client_assignment.read" in management_scopes
    assert "client_assignment.write" in management_scopes
    assert "group_assignment.read" not in management_scopes
    assert "group_assignment.write" not in management_scopes
