"""Shared authorization and request helpers for Projects S2S resource routes."""

import hashlib
from uuid import UUID

from aws_lambda_powertools.event_handler import api_gateway

from app.projects.entrypoints.s2s_api import s2s_exception
from app.shared.domain.ports.idempotency_service import IdempotencyScope

NO_STORE = {"Cache-Control": "no-store"}


def hash_idempotency_key(key: UUID) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()


def client_id(router: api_gateway.Router) -> str:
    return router.context["user_principal"].user_name


def require_access_and_scope(
    router: api_gateway.Router,
    dependencies,
    client_id: str,
    project_id: str,
    scope: str,
) -> None:
    """Verify assignment and the exact OAuth scope before any resource lookup."""
    try:
        assignment = dependencies.projects_query_service.get_service_client_assignment(project_id, client_id)
    except Exception as error:
        raise s2s_exception.ProjectAccessUnavailable() from error
    if assignment is None or getattr(assignment.status, "value", assignment.status) != "ACTIVE":
        raise s2s_exception.ProjectAccessDenied()

    require_scope(router, scope)


def require_scope(router: api_gateway.Router, required_scope: str) -> None:
    claims = router.current_event.raw_event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    granted_scopes = set(str(claims.get("scope", "")).split())
    if required_scope not in granted_scopes:
        raise s2s_exception.InsufficientScope()


def authorize(router: api_gateway.Router, dependencies, project_id: str, required_scope: str) -> str:
    current_client_id = client_id(router)
    require_access_and_scope(router, dependencies, current_client_id, project_id, required_scope)
    return current_client_id


def idempotency_scope(
    router: api_gateway.Router,
    client_id: str,
    project_id: str,
    operation: str,
    parent_resource_id: str | None = None,
) -> IdempotencyScope:
    headers = router.current_event.raw_event.get("headers") or {}
    raw_key = next(
        (value for name, value in headers.items() if name.lower() == "idempotency-key"),
        None,
    )
    if raw_key is None:
        multi_headers = router.current_event.raw_event.get("multiValueHeaders") or {}
        values = next(
            (value for name, value in multi_headers.items() if name.lower() == "idempotency-key"),
            None,
        )
        raw_key = values[0] if isinstance(values, list) and values else None
    try:
        key = UUID(raw_key) if isinstance(raw_key, str) else None
    except (ValueError, AttributeError, TypeError):
        key = None
    if key is None:
        raise s2s_exception.InvalidIdempotencyKey()
    return IdempotencyScope(client_id, project_id, operation, parent_resource_id, key)
