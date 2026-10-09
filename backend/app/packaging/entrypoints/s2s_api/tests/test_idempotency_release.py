from datetime import UTC, datetime
from http import HTTPStatus
from unittest import mock
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.packaging.domain.exceptions.domain_exception import DomainException
from app.packaging.domain.exceptions.s2s_exception import ResourceReadNotReady
from app.packaging.entrypoints.s2s_api import idempotency
from app.shared.domain.ports.idempotency_service import IdempotencyScope, Reservation, ReservationOutcome

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SCOPE = IdempotencyScope("client-1", "proj-1", "CREATE_COMPONENT", None, UUID("b39cdd55-774d-4bc3-81a8-70f23a03c485"))


class Request(BaseModel):
    name: str


def execute(service, *, create, resource_exists=lambda _: False):
    return idempotency.execute_create(
        service=service,
        scope=SCOPE,
        request=Request(name="component"),
        resource_id="comp-new",
        resource_exists=resource_exists,
        response_for_id=lambda resource_id: idempotency.StoredCreateResponse(HTTPStatus.OK, {"id": resource_id}),
        create=create,
        now=NOW,
    )


def reserving(outcome, resource_id="comp-new"):
    service = mock.Mock()
    service.reserve.return_value = Reservation(outcome, resource_id)
    return service


def test_an_unexpected_create_failure_releases_the_reservation():
    service = reserving(ReservationOutcome.ACQUIRED)

    with pytest.raises(RuntimeError):
        execute(service, create=mock.Mock(side_effect=RuntimeError("boom")))

    service.release.assert_called_once_with(SCOPE, mock.ANY, "comp-new", NOW)
    service.complete.assert_not_called()


def test_a_failed_recovery_read_releases_the_reservation():
    service = reserving(ReservationOutcome.RECOVER, "comp-earlier")

    with pytest.raises(ResourceReadNotReady):
        execute(service, create=mock.Mock(), resource_exists=mock.Mock(side_effect=RuntimeError("throttled")))

    service.release.assert_called_once_with(SCOPE, mock.ANY, "comp-earlier", NOW)


def test_a_domain_failure_is_stored_for_replay_not_released():
    service = reserving(ReservationOutcome.ACQUIRED)

    with pytest.raises(DomainException):
        execute(service, create=mock.Mock(side_effect=DomainException("invalid")))

    service.release.assert_not_called()
    assert service.complete.call_args.args[3] == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_release_failure_does_not_hide_the_create_failure():
    service = reserving(ReservationOutcome.ACQUIRED)
    service.release.side_effect = RuntimeError("dynamodb unavailable")

    with pytest.raises(RuntimeError, match="boom"):
        execute(service, create=mock.Mock(side_effect=RuntimeError("boom")))
