"""Externally managed projects are read-only for signed-in users."""

import json

import pytest
from assertpy import assert_that
from aws_lambda_powertools.event_handler import api_gateway

from app.shared.middleware import authorization, externally_managed

SOURCE = "example-org/config programs/example"


def _app() -> api_gateway.APIGatewayRestResolver:
    app = api_gateway.APIGatewayRestResolver()
    app.use(
        middlewares=[
            authorization.require_auth_context,
            externally_managed.refuse_changes_to_managed_projects(
                allowed=[("POST", r"/projects/\{projectId\}/components/\{componentId\}/validate-version")]
            ),
        ]
    )

    @app.get("/projects/<project_id>/recipes")
    def list_recipes(project_id: str):
        return {"ok": True}

    @app.post("/projects/<project_id>/recipes")
    def create_recipe(project_id: str):
        return {"ok": True}

    @app.post("/projects/<project_id>/components/<component_id>/validate-version")
    def validate(project_id: str, component_id: str):
        return {"ok": True}

    return app


def _event(method: str, path: str, resource: str, authorizer: dict | None = None, identity: dict | None = None):
    context: dict = {}
    if authorizer is not None:
        context["authorizer"] = authorizer
    if identity is not None:
        context["identity"] = identity
    return {
        "httpMethod": method,
        "path": path,
        "resource": resource,
        "headers": {},
        "queryStringParameters": None,
        "pathParameters": None,
        "body": None,
        "isBase64Encoded": False,
        "requestContext": context,
    }


def _user(managed_by: str = "terraform") -> dict:
    return {
        "userName": "T0000001",
        "userEmail": "user@example.com",
        "stages": "[]",
        "userRoles": '["ADMIN"]',
        "userDomains": "[]",
        "projectManagedBy": managed_by,
        "projectManagedSource": SOURCE,
    }


def _call(event: dict) -> dict:
    return _app().resolve(event, {})


def test_a_signed_in_users_change_to_a_managed_project_is_refused():
    response = _call(_event("POST", "/projects/proj-1/recipes", "/projects/{projectId}/recipes", _user()))

    assert_that(response["statusCode"]).is_equal_to(409)
    body = json.loads(response["body"])
    assert_that(body["code"]).is_equal_to("PROJECT_EXTERNALLY_MANAGED")
    assert_that(body["message"]).contains(SOURCE)


def test_platform_admins_are_refused_too():
    # No portal bypass: changes go through the managing tool.
    response = _call(_event("POST", "/projects/proj-1/recipes", "/projects/{projectId}/recipes", _user()))

    assert_that(response["statusCode"]).is_equal_to(409)


def test_reads_of_a_managed_project_pass():
    response = _call(_event("GET", "/projects/proj-1/recipes", "/projects/{projectId}/recipes", _user()))

    assert_that(response["statusCode"]).is_equal_to(200)


def test_an_allowed_write_passes():
    response = _call(
        _event(
            "POST",
            "/projects/proj-1/components/comp-1/validate-version",
            "/projects/{projectId}/components/{componentId}/validate-version",
            _user(),
        )
    )

    assert_that(response["statusCode"]).is_equal_to(200)


@pytest.mark.parametrize("managed_by", [""])
def test_projects_managed_in_the_portal_are_not_locked(managed_by):
    response = _call(_event("POST", "/projects/proj-1/recipes", "/projects/{projectId}/recipes", _user(managed_by)))

    assert_that(response["statusCode"]).is_equal_to(200)


def test_internal_iam_calls_between_bounded_contexts_pass():
    response = _call(
        _event(
            "POST",
            "/projects/proj-1/recipes",
            "/projects/{projectId}/recipes",
            identity={"user": "AROAEXAMPLE:publishing-handler", "accountId": "389339913074"},
        )
    )

    assert_that(response["statusCode"]).is_equal_to(200)


def test_service_clients_pass():
    # A service client on an S2S API: the lock is for the portal, not for the tool that manages the project.
    authorizer = {"claims": {"client_id": "terraform-client", "scope": "clients/publishing/version.promote"}}
    response = _call(_event("POST", "/projects/proj-1/recipes", "/projects/{projectId}/recipes", authorizer))

    assert_that(response["statusCode"]).is_equal_to(200)
