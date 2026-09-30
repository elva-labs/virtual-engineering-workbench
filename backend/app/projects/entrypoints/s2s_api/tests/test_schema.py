import assertpy
from openapi_spec_validator import validate_spec

from app.shared.test_utils import openapi_analyzer


def test_api_schema_should_be_valid(api_schema):
    # ACT / ASSERT
    validate_spec(api_schema)


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


def test_service_client_assignment_routes_use_dedicated_scopes(api_schema):
    operations = api_schema["paths"]["/projects/{projectId}/clients/{clientId}"]

    assert operations["get"]["security"] == [{"ClientCredentials": ["clients/projects/client_assignment.read"]}]
    for method in ("put", "delete"):
        assert operations[method]["security"] == [{"ClientCredentials": ["clients/projects/client_assignment.write"]}]


def test_technology_routes_use_dedicated_scopes_and_idempotent_create(api_schema):
    paths = api_schema["paths"]
    assert paths["/projects/{projectId}/technologies"]["get"]["security"] == [
        {"ClientCredentials": ["clients/projects/technology.read"]}
    ]
    create = paths["/projects/{projectId}/technologies"]["post"]
    assert create["security"] == [{"ClientCredentials": ["clients/projects/technology.write"]}]
    assert create["parameters"][-1]["name"] == "Idempotency-Key"
    assert "201" in create["responses"]
    assert paths["/projects/{projectId}/technologies/{technologyId}"]["get"]["security"] == [
        {"ClientCredentials": ["clients/projects/technology.read"]}
    ]
    for method in ("put", "delete"):
        assert paths["/projects/{projectId}/technologies/{technologyId}"][method]["security"] == [
            {"ClientCredentials": ["clients/projects/technology.write"]}
        ]
    assert paths["/projects/{projectId}/technologies/{technologyId}"]["delete"]["responses"]["409"]
    for request_schema in ("CreateProjectTechnologyRequest", "UpdateProjectTechnologyRequest"):
        assert api_schema["components"]["schemas"][request_schema]["properties"]["name"]["pattern"] == r"\S"


def test_project_account_routes_use_dedicated_scopes_and_async_headers(api_schema):
    paths = api_schema["paths"]
    assert paths["/projects/{projectId}/accounts"]["get"]["security"] == [
        {"ClientCredentials": ["clients/projects/account.read"]}
    ]
    create = paths["/projects/{projectId}/accounts"]["post"]
    assert create["security"] == [{"ClientCredentials": ["clients/projects/account.write"]}]
    assert create["parameters"][-1]["name"] == "Idempotency-Key"
    assert create["responses"]["202"]["headers"]["Retry-After"]["schema"]["example"] == 5
    account_path = paths["/projects/{projectId}/accounts/{accountId}"]
    assert account_path["get"]["security"] == [{"ClientCredentials": ["clients/projects/account.read"]}]
    assert account_path["put"]["security"] == [{"ClientCredentials": ["clients/projects/account.write"]}]
    assert account_path["put"]["responses"]["202"]["headers"]["Retry-After"]["schema"]["example"] == 5
    assert account_path["put"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/ProjectAccount"
    )
    assert account_path["delete"]["security"] == [{"ClientCredentials": ["clients/projects/account.write"]}]
    assert set(account_path["delete"]["responses"]) == {"204", "403", "409"}


def test_project_account_schema_excludes_runtime_parameters_and_problem_is_sanitized(api_schema):
    schemas = api_schema["components"]["schemas"]
    assert "parameters" not in schemas["ProjectAccount"]["properties"]
    for field in ("name", "description", "technologyId", "region", "status"):
        assert schemas["ProjectAccount"]["properties"][field]["nullable"] is True
    assert set(schemas["ProblemDetails"]["required"]) == {"code", "requestId", "retryable"}


def test_management_routes_use_dedicated_scopes(api_schema):
    paths = api_schema["paths"]
    assert paths["/projects"]["post"]["security"] == [{"ClientCredentials": ["clients/projects/program.write"]}]
    for method in ("put", "delete"):
        assert paths["/projects/{projectId}"][method]["security"] == [{"ClientCredentials": ["clients/projects/program.write"]}]
    assert paths["/projects/{projectId}"]["get"]["security"] == [{"ClientCredentials": ["clients/projects/program.read"]}]
    for method in ("get",):
        assert paths["/projects/{projectId}/groups/{groupId}"][method]["security"] == [{"ClientCredentials": ["clients/projects/group_assignment.read"]}]
    for method in ("put", "delete"):
        assert paths["/projects/{projectId}/groups/{groupId}"][method]["security"] == [{"ClientCredentials": ["clients/projects/group_assignment.write"]}]
