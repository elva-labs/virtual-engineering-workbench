import aws_cdk
from aws_cdk.assertions import Template

from infra import config
from infra.backend.integration_oauth_stack import IntegrationOauthStack
from infra.backend.projects_app_stack import S2S_CACHE_EXPLICIT_DISABLE


def projects_oauth_template() -> Template:
    app = aws_cdk.App()
    app_config = config.AppConfig(
        account="111111111111",
        region="eu-west-1",
        environment="dev",
        web_app_account="111111111111",
        image_service_account="222222222222",
        catalog_service_account="333333333333",
        component_name="projects",
        environment_config=config.env_config["dev"],
        component_specific=config.projects_app_config["dev"],
    )
    stack = IntegrationOauthStack(
        app,
        "ProjectsOAuthTest",
        app_config=app_config,
        env=aws_cdk.Environment(account="111111111111", region="eu-west-1"),
    )
    return Template.from_stack(stack)


def test_projects_resource_server_exposes_technology_and_account_scopes():
    template = projects_oauth_template()
    servers = template.find_resources("AWS::Cognito::UserPoolResourceServer")
    projects = next(
        server["Properties"] for server in servers.values() if server["Properties"]["Identifier"] == "clients/projects"
    )
    scopes = {scope["ScopeName"] for scope in projects["Scopes"]}
    assert {"technology.read", "technology.write", "account.read", "account.write"} <= scopes


def test_sample_s2s_client_is_granted_technology_and_account_scopes():
    template = projects_oauth_template()
    resource_servers = template.find_resources("AWS::Cognito::UserPoolResourceServer")
    projects_server_id = next(
        resource_id
        for resource_id, server in resource_servers.items()
        if server["Properties"]["Identifier"] == "clients/projects"
    )
    # The orphan-project bootstrap client (project management and Entra group access) is a second,
    # separately scoped client; this test is about the sample S2S client only.
    clients = {
        resource_id: client
        for resource_id, client in template.find_resources("AWS::Cognito::UserPoolClient").items()
        if "bootstrap" not in client["Properties"].get("ClientName", "")
    }
    assert len(clients) == 1

    allowed_scopes = next(iter(clients.values()))["Properties"]["AllowedOAuthScopes"]
    for scope_name in ("technology.read", "technology.write", "account.read", "account.write"):
        assert {"Fn::Join": ["", [{"Ref": projects_server_id}, f"/{scope_name}"]]} in allowed_scopes


def test_projects_s2s_resource_reads_bypass_api_gateway_cache():
    assert S2S_CACHE_EXPLICIT_DISABLE == [
        "/projects/GET",
        "/projects/{projectId}/technologies/GET",
        "/projects/{projectId}/technologies/{technologyId}/GET",
        "/projects/{projectId}/accounts/GET",
        "/projects/{projectId}/accounts/{accountId}/GET",
        "/projects/{projectId}/management/GET",
        "/projects/{projectId}/workbench-lifecycle/GET",
    ]
