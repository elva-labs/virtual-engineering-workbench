from pathlib import Path

import assertpy
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openapi_spec_validator import validate_spec

from app.shared.test_utils import openapi_analyzer


def test_api_schema_should_be_valid(api_schema):
    # ACT / ASSERT
    validate_spec(api_schema)


def test_api_schema_renders_quoted_cors_origin_for_shutdown_policy():
    schema_directory = Path(__file__).parents[1] / "schema"
    template_env = Environment(
        loader=FileSystemLoader(schema_directory), autoescape=select_autoescape(enabled_extensions="yaml")
    )
    template = template_env.get_template("proserve-workbench-projects-api-schema.yaml")
    # Infra supplies API Gateway's single-quoted static header expression, not a bare domain.
    cors_origin = "'https://dev.workbench.elva.land'"
    rendered = template.render(
        handler_arn="arn:aws:lambda:eu-north-1:123456789012:function:projects",
        authorization_bc_handler_arn="arn:aws:lambda:eu-north-1:123456789012:function:authorization",
        apigw_invocation_role_arn="arn:aws:iam::123456789012:role/api-gateway",
        cors_origin=cors_origin,
        handler_region="eu-north-1",
    )

    schema = yaml.safe_load(rendered)
    validate_spec(schema)
    preflight = schema["paths"]["/projects/{projectId}/workbench-lifecycle"]["options"]
    response_parameters = preflight["x-amazon-apigateway-integration"]["responses"]["default"]["responseParameters"]
    assert response_parameters["method.response.header.Access-Control-Allow-Origin"] == cors_origin
    assert response_parameters["method.response.header.Access-Control-Allow-Methods"] == "'GET,PUT,DELETE,OPTIONS'"


def test_api_schema_should_have_http_method_cors_configured(api_schema):
    # ARRANGE
    path_contexts = openapi_analyzer.OpenAPIAnalyzer.from_dict(api_schema).path_contexts

    # ACT
    result = {
        path_context.path: path_context.cors_method_error
        for path_context in path_contexts
        if path_context.cors_method_error
    }

    # ASSERT
    assertpy.assert_that(result).described_as("HTTP methods not configured with CORS").is_empty()


def test_api_schema_should_have_header_cors_configured(api_schema):
    # ARRANGE
    path_contexts = openapi_analyzer.OpenAPIAnalyzer.from_dict(api_schema).path_contexts

    # ACT
    result = {
        path_context.path: path_context.cors_header_error
        for path_context in path_contexts
        if path_context.cors_header_error
    }

    # ASSERT
    assertpy.assert_that(result).described_as("HTTP headers not configured with CORS").is_empty()


def test_api_schema_should_have_correct_roles_configured(api_schema):
    # ARRANGE
    path_contexts = openapi_analyzer.OpenAPIAnalyzer.from_dict(api_schema).path_contexts

    # ACT
    incorrect_roles = {ctx.path: ctx.tag_name_errors for ctx in path_contexts if ctx.tag_name_errors}

    # ASSERT
    assertpy.assert_that(incorrect_roles).described_as("Incorrect role names detected").is_empty()


def test_api_schema_should_have_auth_configured(api_schema):
    # ARRANGE
    path_contexts = openapi_analyzer.OpenAPIAnalyzer.from_dict(api_schema).path_contexts

    # ACT
    methods_wo_auth = {ctx.path: ctx.auth_errors for ctx in path_contexts if ctx.auth_errors}

    # ASSERT
    assertpy.assert_that(methods_wo_auth).described_as("Methods without auth detected").is_empty()


def test_internal_service_client_assignment_route_uses_sigv4(api_schema):
    operation = api_schema["paths"]["/internal/projects/{projectId}/clients/{clientId}"]["get"]

    assert operation["security"] == [{"sigv4": []}]


def test_group_routes_and_effective_access_contract(api_schema):
    human = api_schema["paths"]["/projects/{projectId}/groups"]["get"]
    internal = api_schema["paths"]["/internal/projects/{projectId}/groups"]["get"]
    assert human["operationId"] == "GetProjectGroups"
    assert internal["security"] == [{"sigv4": []}]
    assert "effectiveAccess" in api_schema["components"]["schemas"]["GetProjectsResponse"]["properties"]


def test_shutdown_policy_actions_match_cedar_and_authenticated_user_api(api_schema):
    from infra.auth.projects_auth_schema import ProjectsBCActions

    operations = api_schema["paths"]["/projects/{projectId}/workbench-lifecycle"]
    expected = {
        "get": ProjectsBCActions.GetProjectWorkbenchLifecycle,
        "put": ProjectsBCActions.UpdateProjectWorkbenchLifecycle,
        "delete": ProjectsBCActions.DeleteProjectWorkbenchLifecycle,
    }
    assert api_schema["security"] == [{"AVP-Request-Authorizer": []}]
    for method, action in expected.items():
        operation = operations[method]
        assert operation["operationId"] == action
        assert "security" not in operation
        assert operation["x-amazon-apigateway-integration"]["type"] == "aws_proxy"
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ProjectWorkbenchLifecycleResponse"
        }
    policy = api_schema["components"]["schemas"]["ProjectWorkbenchLifecycle"]
    for field in ["idleStopMinutes", "userIdleTimeoutMinMinutes", "userIdleTimeoutMaxMinutes"]:
        assert policy["properties"][field]["minimum"] == 10
        assert policy["properties"][field]["maximum"] == 1440
