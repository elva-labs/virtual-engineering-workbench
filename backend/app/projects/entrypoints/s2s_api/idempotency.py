"""Projects-specific create idempotency orchestration."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Callable

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from pydantic import BaseModel

from app.projects.domain.exceptions import domain_exception
from app.projects.entrypoints.s2s_api import problem_details, s2s_exception
from app.shared.domain.ports.idempotency_service import (
    IdempotencyScope,
    IdempotencyService,
    Reservation,
    ReservationOutcome,
)

logger = Logger()
metrics = Metrics(service="ProjectsS2S")


@dataclass(frozen=True)
class StoredCreateResponse:
    status_code: int
    body: dict


def canonical_request_hash(request: BaseModel) -> str:
    body = request.model_dump(mode="json", by_alias=True, exclude_none=False)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _complete(service, scope, request_hash, resource_id, result, now):
    service.complete(scope, request_hash, resource_id, result.status_code, result.body, now)


def _release(service, scope, request_hash, resource_id, now):
    # An unexpected failure (a 500) must not block the key until the lease ends: a retry recovers at
    # once, checking first whether the resource was created.
    try:
        service.release(scope, request_hash, resource_id, now)
    except Exception:
        logger.exception("Could not release the idempotency reservation", operation=scope.operation)


def _replay(reservation: Reservation) -> StoredCreateResponse:
    if reservation.response_status is None or reservation.response_body is None:
        raise RuntimeError("Completed idempotency record has no response")
    if reservation.response_status >= HTTPStatus.BAD_REQUEST:
        raise s2s_exception.ReplayedCreateFailure(
            reservation.response_status,
            reservation.response_body["detail"],
            reservation.response_body["code"],
            bool(reservation.response_body["retryable"]),
        )
    return StoredCreateResponse(reservation.response_status, reservation.response_body)


def execute_create(  # noqa: C901
    *,
    service: IdempotencyService,
    scope: IdempotencyScope,
    request: BaseModel,
    resource_id: str,
    resource_exists: Callable[[str], bool],
    response_for_id: Callable[[str], StoredCreateResponse],
    create: Callable[[str], StoredCreateResponse],
    now: datetime,
    resume_existing: Callable[[str], None] | None = None,
) -> StoredCreateResponse:
    request_hash = canonical_request_hash(request)
    reservation = service.reserve(scope, request_hash, resource_id, now)
    metric_names = {
        ReservationOutcome.ACQUIRED: "IdempotencyReservations",
        ReservationOutcome.REPLAY: "IdempotencyReplays",
        ReservationOutcome.CONFLICT: "IdempotencyConflicts",
        ReservationOutcome.IN_PROGRESS: "IdempotencyInProgress",
        ReservationOutcome.RECOVER: "IdempotencyRecoveries",
    }
    metrics.add_metric(name=metric_names[reservation.outcome], unit=MetricUnit.Count, value=1)
    logger.info(
        "Projects idempotency reservation",
        operation=scope.operation,
        outcome=reservation.outcome.value,
    )

    if reservation.outcome is ReservationOutcome.REPLAY:
        return _replay(reservation)
    if reservation.outcome is ReservationOutcome.CONFLICT:
        raise s2s_exception.IdempotencyKeyReused()
    if reservation.outcome is ReservationOutcome.IN_PROGRESS:
        raise s2s_exception.IdempotencyRequestInProgress()

    if reservation.outcome is ReservationOutcome.RECOVER:
        try:
            exists = resource_exists(reservation.resource_id)
        except Exception as error:
            _release(service, scope, request_hash, reservation.resource_id, now)
            raise s2s_exception.ResourceReadNotReady() from error
        if exists:
            if resume_existing is not None:
                resume_existing(reservation.resource_id)
            result = response_for_id(reservation.resource_id)
            _complete(service, scope, request_hash, reservation.resource_id, result, now)
            return result

    try:
        result = create(reservation.resource_id)
    except s2s_exception.S2SException as error:
        if error.retryable:
            _release(service, scope, request_hash, reservation.resource_id, now)
            raise
        failure = StoredCreateResponse(
            problem_details.status_for(error),
            {
                "detail": error.detail,
                "code": error.code,
                "retryable": False,
            },
        )
        _complete(service, scope, request_hash, reservation.resource_id, failure, now)
        raise s2s_exception.ReplayedCreateFailure(
            failure.status_code,
            error.detail,
            error.code,
            retryable=False,
        ) from error
    except domain_exception.DomainException as error:
        failure = StoredCreateResponse(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            {
                "detail": "The request failed Projects validation.",
                "code": "DOMAIN_VALIDATION_FAILED",
                "retryable": False,
            },
        )
        _complete(service, scope, request_hash, reservation.resource_id, failure, now)
        raise s2s_exception.ReplayedCreateFailure(
            failure.status_code,
            failure.body["detail"],
            failure.body["code"],
            False,
        ) from error
    except Exception:
        _release(service, scope, request_hash, reservation.resource_id, now)
        raise
    _complete(service, scope, request_hash, reservation.resource_id, result, now)
    return result
