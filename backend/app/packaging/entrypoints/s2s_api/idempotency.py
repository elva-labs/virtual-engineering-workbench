import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Callable

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from pydantic import BaseModel

from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.exceptions.s2s_exception import (
    IdempotencyKeyReused,
    IdempotencyRequestInProgress,
    ReplayedCreateFailure,
    ResourceReadNotReady,
)
from app.packaging.domain.ports.idempotency_service import (
    IdempotencyScope,
    IdempotencyService,
    Reservation,
    ReservationOutcome,
)

logger = Logger()
metrics = Metrics(service="PackagingS2S")

OUTCOME_METRICS = {
    ReservationOutcome.ACQUIRED: "IdempotencyReservations",
    ReservationOutcome.REPLAY: "IdempotencyReplays",
    ReservationOutcome.CONFLICT: "IdempotencyConflicts",
    ReservationOutcome.IN_PROGRESS: "IdempotencyInProgress",
    ReservationOutcome.RECOVER: "IdempotencyRecoveries",
}


@dataclass(frozen=True)
class StoredCreateResponse:
    status_code: int
    body: dict


def canonical_request_hash(request: BaseModel) -> str:
    body = request.model_dump(mode="json", by_alias=True, exclude_none=False)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_reservation(scope: IdempotencyScope, reservation: Reservation) -> None:
    metrics.add_metric(name=OUTCOME_METRICS[reservation.outcome], unit=MetricUnit.Count, value=1)
    if reservation.replaced_expired:
        metrics.add_metric(name="IdempotencyRecordExpiries", unit=MetricUnit.Count, value=1)
    logger.info(
        "Idempotency reservation",
        operation=scope.operation,
        projectId=scope.project_id,
        clientId=scope.client_id,
        idempotencyKeyDigest=hashlib.sha256(str(scope.key).encode("utf-8")).hexdigest(),
        generatedResourceId=reservation.resource_id,
        outcome=reservation.outcome.value,
    )


def _replay_response(reservation: Reservation) -> StoredCreateResponse:
    if reservation.response_status is None or reservation.response_body is None:
        raise RuntimeError("Completed idempotency record has no stored response")
    if reservation.response_status >= HTTPStatus.BAD_REQUEST:
        raise ReplayedCreateFailure(
            status_code=reservation.response_status,
            detail=str(reservation.response_body["detail"]),
            code=str(reservation.response_body["code"]),
            retryable=bool(reservation.response_body["retryable"]),
        )
    return StoredCreateResponse(reservation.response_status, reservation.response_body)


def _complete(
    service: IdempotencyService,
    scope: IdempotencyScope,
    request_hash: str,
    resource_id: str,
    result: StoredCreateResponse,
    now: datetime,
) -> None:
    service.complete(
        scope,
        request_hash,
        resource_id,
        result.status_code,
        result.body,
        now,
    )


def _recover_response(
    *,
    service: IdempotencyService,
    scope: IdempotencyScope,
    request_hash: str,
    reservation: Reservation,
    resource_exists: Callable[[str], bool],
    response_for_id: Callable[[str], StoredCreateResponse],
    resume_existing: Callable[[str], None] | None,
    now: datetime,
) -> StoredCreateResponse | None:
    try:
        exists = resource_exists(reservation.resource_id)
    except Exception as error:
        raise ResourceReadNotReady() from error
    if not exists:
        return None
    if resume_existing is not None:
        resume_existing(reservation.resource_id)
    result = response_for_id(reservation.resource_id)
    _complete(service, scope, request_hash, reservation.resource_id, result, now)
    return result


def _create_response(
    *,
    service: IdempotencyService,
    scope: IdempotencyScope,
    request_hash: str,
    resource_id: str,
    create: Callable[[str], StoredCreateResponse],
    now: datetime,
) -> StoredCreateResponse:
    try:
        result = create(resource_id)
    except domain_exception.DomainException as error:
        failure = StoredCreateResponse(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            {
                "detail": str(error),
                "code": "DOMAIN_VALIDATION_FAILED",
                "retryable": False,
            },
        )
        _complete(service, scope, request_hash, resource_id, failure, now)
        raise
    _complete(service, scope, request_hash, resource_id, result, now)
    return result


def execute_create(
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
    _record_reservation(scope, reservation)

    if reservation.outcome is ReservationOutcome.REPLAY:
        return _replay_response(reservation)
    if reservation.outcome is ReservationOutcome.CONFLICT:
        raise IdempotencyKeyReused()
    if reservation.outcome is ReservationOutcome.IN_PROGRESS:
        raise IdempotencyRequestInProgress()

    if reservation.outcome is ReservationOutcome.RECOVER:
        recovered = _recover_response(
            service=service,
            scope=scope,
            request_hash=request_hash,
            reservation=reservation,
            resource_exists=resource_exists,
            response_for_id=response_for_id,
            resume_existing=resume_existing,
            now=now,
        )
        if recovered is not None:
            return recovered

    return _create_response(
        service=service,
        scope=scope,
        request_hash=request_hash,
        resource_id=reservation.resource_id,
        create=create,
        now=now,
    )
