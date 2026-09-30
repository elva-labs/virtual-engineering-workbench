"""Shared authorization and request helpers for Publishing S2S routes."""

from uuid import UUID

from aws_lambda_powertools.event_handler import api_gateway

from app.publishing.domain.exceptions import s2s_exception
from app.shared.domain.ports.idempotency_service import IdempotencyScope

NO_STORE = {"Cache-Control": "no-store"}


def client_id(router: api_gateway.Router) -> str:
    return router.context["user_principal"].user_name


def require_scope(router: api_gateway.Router, required_scope: str) -> None:
    claims = router.current_event.raw_event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    if required_scope not in set(str(claims.get("scope", "")).split()):
        raise s2s_exception.InsufficientScope()


def authorize(router: api_gateway.Router, dependencies, project_id: str, required_scope: str) -> str:
    """Project assignment first, then the exact scope - before any resource is looked up."""
    current_client_id = client_id(router)
    dependencies.project_access_service.require_access(current_client_id, project_id)
    require_scope(router, required_scope)
    return current_client_id


def idempotency_scope(
    router: api_gateway.Router,
    client_id: str,
    project_id: str,
    operation: str,
    parent_resource_id: str | None = None,
) -> IdempotencyScope:
    event = router.current_event.raw_event
    raw_key = next((v for k, v in (event.get("headers") or {}).items() if k.lower() == "idempotency-key"), None)
    if raw_key is None:
        values = next(
            (v for k, v in (event.get("multiValueHeaders") or {}).items() if k.lower() == "idempotency-key"), None
        )
        raw_key = values[0] if isinstance(values, list) and values else None
    try:
        key = UUID(raw_key) if isinstance(raw_key, str) else None
    except (ValueError, AttributeError, TypeError):
        key = None
    if key is None:
        raise s2s_exception.InvalidIdempotencyKey()
    return IdempotencyScope(client_id, project_id, operation, parent_resource_id, key)
