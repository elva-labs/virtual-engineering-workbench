"""Read-only platform clients of the S2S APIs (optional; none by default).

A deployment may name Cognito app clients that read every project without a client assignment, for example
the client of a plan-only Terraform run that refreshes every program's state but must never change one. The
S2S Lambdas get their ids in S2S_READ_ONLY_PLATFORM_CLIENT_IDS (comma separated; unset or empty = none, the
upstream behaviour).

Such a client is admitted to a project without an ACTIVE assignment only for a safe method (GET, HEAD) on a
route whose required scope is a read scope (`<server>/<resource>.read`), and its token must still hold that
scope. Every other request from it (POST, PUT, PATCH, DELETE) is refused before any route runs, whatever its
token holds. Every other client is unaffected: a project route needs its ACTIVE assignment, as before.
"""

import os
from typing import Callable

from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response
from aws_lambda_powertools.event_handler.middlewares import NextMiddleware

from app.shared.middleware import authorization

ENVIRONMENT_VARIABLE = "S2S_READ_ONLY_PLATFORM_CLIENT_IDS"
SAFE_METHODS = frozenset({"GET", "HEAD"})
READ_SCOPE_SUFFIX = ".read"


def client_ids() -> frozenset[str]:
    """The configured read-only platform client ids; read on every call, so a test can set them."""
    return frozenset(item.strip() for item in os.environ.get(ENVIRONMENT_VARIABLE, "").split(",") if item.strip())


def is_read_only_platform_client(principal: authorization.Principal | None) -> bool:
    """A service client (Cognito OAuth token) whose client id is configured as read-only."""
    ids = client_ids()
    # None configured (the default): nobody is one, and nothing else is looked at.
    if not ids or principal is None:
        return False
    return (
        getattr(principal, "user_name", None) in ids
        and getattr(principal, "auth_type", None) == authorization.AuthType.CognitoServiceJWT
    )


def is_read_scope(scope: str) -> bool:
    return scope.endswith(READ_SCOPE_SUFFIX)


def _http_method(event) -> str:
    method = getattr(event, "http_method", None)
    if method is None and isinstance(event, dict):
        method = event.get("httpMethod")
    return (method or "").upper()


def admits_without_assignment(principal: authorization.Principal | None, event, scope: str) -> bool:
    """True only for a read-only platform client's safe request to a route that needs a read scope.

    event: the API Gateway event (router.current_event). The caller still checks that the token holds the
    scope. False for everyone else, who keep needing an ACTIVE client assignment."""
    return is_read_only_platform_client(principal) and _http_method(event) in SAFE_METHODS and is_read_scope(scope)


def refuse_writes(
    refusal: Callable[[], Exception],
) -> Callable[[APIGatewayRestResolver, NextMiddleware], Response]:
    """Middleware for an S2S API, after authorization.require_auth_context.

    Raises refusal() (the API's 403 problem) for any request of a read-only platform client that isn't a safe
    method, before any route or lookup runs. Nothing changes for other clients."""

    def middleware(app: APIGatewayRestResolver, next_middleware: NextMiddleware) -> Response:
        principal = app.context.get("user_principal")
        if is_read_only_platform_client(principal) and _http_method(app.current_event) not in SAFE_METHODS:
            raise refusal()
        return next_middleware(app)

    return middleware
