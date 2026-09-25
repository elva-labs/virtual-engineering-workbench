from uuid import UUID

from aws_lambda_powertools import Metrics
from aws_lambda_powertools.event_handler import api_gateway

from app.packaging.domain.exceptions.s2s_exception import InsufficientScope, InvalidIdempotencyKey
from app.packaging.domain.ports.idempotency_service import IdempotencyScope

NO_STORE = {"Cache-Control": "no-store"}
api_metrics = Metrics(service="PackagingS2S")


def client_id(router: api_gateway.Router) -> str:
    return router.context["user_principal"].user_name


def require_scope(router: api_gateway.Router, required_scope: str) -> None:
    claims = router.current_event.raw_event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    granted_scopes = set(str(claims.get("scope", "")).split())
    if required_scope not in granted_scopes:
        raise InsufficientScope(required_scope)


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
        multi_value_headers = router.current_event.raw_event.get("multiValueHeaders") or {}
        values = next(
            (value for name, value in multi_value_headers.items() if name.lower() == "idempotency-key"),
            None,
        )
        raw_key = values[0] if isinstance(values, list) and values else None
    try:
        key = UUID(raw_key) if isinstance(raw_key, str) else None
    except (ValueError, AttributeError, TypeError):
        key = None
    if key is None:
        raise InvalidIdempotencyKey()
    return IdempotencyScope(
        client_id=client_id,
        project_id=project_id,
        operation=operation,
        parent_resource_id=parent_resource_id,
        key=key,
    )
