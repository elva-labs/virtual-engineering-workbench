import json
from pathlib import Path
from types import SimpleNamespace

import aws_cdk
import pytest
import yaml
from aws_cdk import aws_ssm
from aws_cdk.assertions import Template

from infra import config
from infra.auth import projects_auth, projects_auth_schema
from infra.constructs import oidc_group_grants


@pytest.mark.parametrize("enabled", [False, True])
def test_provider_is_imported_from_oidc_setup_only_when_enabled(enabled):
    app = aws_cdk.App()
    stack = aws_cdk.Stack(app, "Groups")
    app_config = SimpleNamespace(
        environment="dev", environment_config={**config.env_config["dev"], "oidc-groups-enabled": enabled}
    )
    provider = oidc_group_grants.provider_name(stack, app_config)
    aws_ssm.StringParameter(stack, "Result", parameter_name="/test/result", string_value=provider or "disabled")
    template = Template.from_stack(stack).to_json()
    if enabled:
        parameters = template["Parameters"].values()
        assert any(p.get("Default", "").endswith("-ui-dev/oidc-groups-provider-name") for p in parameters)
    else:
        assert "oidc-groups-provider-name" not in json.dumps(template)
        assert provider == ""


def test_binding_actions_use_exactly_the_user_role_management_policy():
    existing_action = projects_auth_schema.ProjectsBCActions.ReAssignProjectUsers
    for action in (
        projects_auth_schema.ProjectsBCActions.GetProjectGroups,
        projects_auth_schema.ProjectsBCActions.PutProjectGroup,
        projects_auth_schema.ProjectsBCActions.DeleteProjectGroup,
    ):
        policies = [p.statement for p in projects_auth.projects_bc_auth_policies]
        assert [i for i, p in enumerate(policies) if f'"{action}"' in p] == [
            i for i, p in enumerate(policies) if f'"{existing_action}"' in p
        ]
        assert action in projects_auth_schema.projects_bc_actions


def test_binding_schema_routes_are_integrated_and_match_authorization_actions():
    schema = yaml.safe_load(
        Path("app/projects/entrypoints/api/schema/proserve-workbench-projects-api-schema.yaml").read_text()
    )
    routes = schema["paths"]["/projects/{projectId}/groups"]
    for method in ["get", "put", "delete"]:
        route = routes[method]
        assert route["operationId"] in projects_auth_schema.projects_bc_actions
        assert route["x-amazon-apigateway-integration"]["type"] == "aws_proxy"
    assert routes["options"]["security"] == []
    internal = schema["paths"]["/internal/projects/{projectId}/groups"]["get"]
    assert internal["security"] == [{"sigv4": []}]
    assert internal["parameters"][1]["name"] == "includeDeleted"
