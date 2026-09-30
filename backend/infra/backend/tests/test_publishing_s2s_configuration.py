from infra import constants
from infra.backend import publishing_app_stack
from infra.backend.tests.test_packaging_s2s_configuration import packaging_oauth_template

PUBLISHING_SCOPES = {"product.read", "product.write", "version.read", "version.promote"}


def test_publishing_s2s_path_and_entrypoint():
    assert constants.CUSTOM_DNS_S2S_API_PATH_PUBLISHING == "clients/publishing"
    assert publishing_app_stack.Entrypoint.S2S_API.value == "s2s-api"


def test_publishing_resource_server_exposes_its_scopes_to_the_sample_client():
    template = packaging_oauth_template()
    servers = template.find_resources("AWS::Cognito::UserPoolResourceServer")
    server_ref, publishing = next(
        (key, server["Properties"])
        for key, server in servers.items()
        if server["Properties"]["Identifier"] == "clients/publishing"
    )
    assert {scope["ScopeName"] for scope in publishing["Scopes"]} == PUBLISHING_SCOPES

    # Scopes render as {"Fn::Join": ["", [{"Ref": <resource server>}, "/<scope>"]]}.
    clients = template.find_resources("AWS::Cognito::UserPoolClient")
    holders = {
        client["Properties"]["ClientName"]: sorted(
            scope["Fn::Join"][1][1]
            for scope in client["Properties"].get("AllowedOAuthScopes", [])
            if isinstance(scope, dict) and scope["Fn::Join"][1][0] == {"Ref": server_ref}
        )
        for client in clients.values()
    }
    granted = {name: scopes for name, scopes in holders.items() if scopes}
    assert list(granted.values()) == [sorted(f"/{scope}" for scope in PUBLISHING_SCOPES)]
    assert all("sample-s2s" in name for name in granted)
