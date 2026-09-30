"""Stable RFC 7807 style responses for Publishing S2S failures."""

import json
from http import HTTPStatus

from app.publishing.domain.exceptions import s2s_exception

PROBLEM_CONTENT_TYPE = "application/problem+json"


def status_for(error: s2s_exception.S2SException) -> HTTPStatus:  # noqa: C901
    if isinstance(error, s2s_exception.InvalidIdempotencyKey):
        return HTTPStatus.BAD_REQUEST
    if isinstance(
        error,
        (
            s2s_exception.IdempotencyKeyReused,
            s2s_exception.IdempotencyRequestInProgress,
            s2s_exception.ResourceConflict,
        ),
    ):
        return HTTPStatus.CONFLICT
    if isinstance(error, s2s_exception.ReplayedCreateFailure):
        return HTTPStatus(error.status_code)
    if isinstance(error, s2s_exception.TechnologyNotFound):
        return HTTPStatus.UNPROCESSABLE_ENTITY
    if isinstance(error, (s2s_exception.InsufficientScope, s2s_exception.ProjectAccessDenied)):
        return HTTPStatus.FORBIDDEN
    if isinstance(
        error,
        (s2s_exception.ProjectAccessUnavailable, s2s_exception.ResourceReadNotReady),
    ):
        return HTTPStatus.SERVICE_UNAVAILABLE
    if isinstance(error, s2s_exception.ResourceNotFound):
        return HTTPStatus.NOT_FOUND
    if isinstance(error, s2s_exception.InvalidRequest):
        return HTTPStatus.BAD_REQUEST
    if isinstance(error, s2s_exception.Unauthorized):
        return HTTPStatus.UNAUTHORIZED
    return HTTPStatus.INTERNAL_SERVER_ERROR


def response(
    status: HTTPStatus,
    *,
    detail: str,
    code: str,
    request_id: str | None,
    retryable: bool,
) -> dict:
    body = {
        "type": f"https://problems.virtual-engineering-workbench.dev/{code.lower().replace('_', '-')}",
        "title": status.phrase,
        "status": int(status),
        "detail": detail,
        "code": code,
        "retryable": retryable,
    }
    if request_id:
        body["requestId"] = request_id
    return {
        "statusCode": int(status),
        "headers": {"Content-Type": PROBLEM_CONTENT_TYPE, "Cache-Control": "no-store"},
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


def api_response(error: s2s_exception.S2SException, request_id: str | None):
    from aws_lambda_powertools.event_handler import api_gateway

    status = status_for(error)
    headers = {"Cache-Control": "no-store"}
    if isinstance(error, s2s_exception.IdempotencyRequestInProgress):
        headers["Retry-After"] = "5"
    return api_gateway.Response(
        status_code=int(status),
        body=response(
            status,
            detail=error.detail,
            code=error.code,
            request_id=request_id,
            retryable=error.retryable,
        )["body"],
        headers=headers,
        content_type=PROBLEM_CONTENT_TYPE,
    )
