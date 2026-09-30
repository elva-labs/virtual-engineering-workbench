"""Externally managed projects are read-only in the portal.

A project whose configuration is owned by an external tool (S2S PUT /projects/{id}/management, for
example by the Terraform configuration that declares it) is changed only there. The authorizer passes
the mark of the project in the request path as projectManagedBy/projectManagedSource; this middleware
refuses the user API's configuration changes of such a project with 409 PROJECT_EXTERNALLY_MANAGED
before any handler runs.

Only signed-in users are refused (Cognito user JWT via the request authorizer). Service clients on the
S2S APIs and internal IAM calls between bounded contexts are not user requests and pass. Reads pass.
Each user API names the writes it still allows (for example validations that change nothing).
"""

import json
import re
from http import HTTPStatus
from typing import Callable, Iterable

from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response, content_types
from aws_lambda_powertools.event_handler.middlewares import NextMiddleware

from app.shared.middleware import authorization

ERROR_CODE = "PROJECT_EXTERNALLY_MANAGED"
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def refusal_message(managed_by: str, source: str) -> str:
    where = f" in {source}" if source else ""
    return f"This project is managed by {managed_by}: change its configuration there{where}."


def _route(app: APIGatewayRestResolver) -> str:
    # The API Gateway resource template (e.g. /projects/{projectId}/recipes), independent of custom
    # domain base paths; the concrete path is the fallback for events without one.
    return app.current_event.get("resource") or app.current_event.path or ""


def refuse_changes_to_managed_projects(
    allowed: Iterable[tuple[str, str]] = (),
) -> Callable[[APIGatewayRestResolver, NextMiddleware], Response]:
    """Middleware for a user API. allowed: (method, route regex) pairs that stay open on managed projects."""
    allowed_routes = [(method.upper(), re.compile(pattern)) for method, pattern in allowed]

    def middleware(app: APIGatewayRestResolver, next_middleware: NextMiddleware) -> Response:
        method = (app.current_event.http_method or "").upper()
        if method in _READ_METHODS:
            return next_middleware(app)

        principal = app.context.get("user_principal")
        if not principal or principal.auth_type != authorization.AuthType.CognitoUserJWT:
            return next_middleware(app)

        auth_context = app.current_event.get("requestContext", {}).get("authorizer") or {}
        managed_by = auth_context.get("projectManagedBy") or ""
        if not managed_by:
            return next_middleware(app)

        route = _route(app)
        if any(method == m and pattern.fullmatch(route) for m, pattern in allowed_routes):
            return next_middleware(app)

        return Response(
            status_code=HTTPStatus.CONFLICT,
            content_type=content_types.APPLICATION_JSON,
            body=json.dumps(
                {
                    "message": refusal_message(managed_by, auth_context.get("projectManagedSource", "")),
                    "code": ERROR_CODE,
                }
            ),
        )

    return middleware
