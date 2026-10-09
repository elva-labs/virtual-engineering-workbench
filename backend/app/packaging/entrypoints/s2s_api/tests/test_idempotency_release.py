from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from unittest import mock
from uuid import UUID

import boto3
import moto
import pytest
from pydantic import BaseModel

from app.packaging.domain.exceptions.domain_exception import DomainException
from app.packaging.domain.exceptions.s2s_exception import ReplayedCreateFailure, ResourceReadNotReady
from app.packaging.entrypoints.s2s_api import idempotency
from app.shared.adapters.idempotency.dynamodb_idempotency_service import DynamoDBIdempotencyService
from app.shared.domain.ports.idempotency_service import IdempotencyScope, Reservation, ReservationOutcome

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SCOPE = IdempotencyScope("client-1", "proj-1", "CREATE_COMPONENT", None, UUID("b39cdd55-774d-4bc3-81a8-70f23a03c485"))


class Request(BaseModel):
    name: str


def respond(resource_id):
    return idempotency.StoredCreateResponse(HTTPStatus.OK, {"id": resource_id})


def execute(
    service,
    *,
    create,
    resource_exists=lambda _: False,
    resume_existing=None,
    resource_id="comp-new",
    now=NOW,
):
    return idempotency.execute_create(
        service=service,
        scope=SCOPE,
        request=Request(name="component"),
        resource_id=resource_id,
        resource_exists=resource_exists,
        response_for_id=respond,
        create=create,
        now=now,
        resume_existing=resume_existing,
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


def test_a_failed_recovery_publication_releases_the_reservation():
    service = reserving(ReservationOutcome.RECOVER, "comp-earlier")

    with pytest.raises(RuntimeError, match="eventbridge"):
        execute(
            service,
            create=mock.Mock(),
            resource_exists=lambda _: True,
            resume_existing=mock.Mock(side_effect=RuntimeError("eventbridge")),
        )

    service.release.assert_called_once_with(SCOPE, mock.ANY, "comp-earlier", NOW)
    service.complete.assert_not_called()


def test_a_failed_recovery_completion_releases_the_reservation():
    service = reserving(ReservationOutcome.RECOVER, "comp-earlier")
    service.complete.side_effect = RuntimeError("throttled")

    with pytest.raises(RuntimeError, match="throttled"):
        execute(service, create=mock.Mock(), resource_exists=lambda _: True)

    service.release.assert_called_once_with(SCOPE, mock.ANY, "comp-earlier", NOW)


def test_a_failed_completion_after_create_releases_the_reservation():
    service = reserving(ReservationOutcome.ACQUIRED)
    service.complete.side_effect = RuntimeError("throttled")

    with pytest.raises(RuntimeError, match="throttled"):
        execute(service, create=mock.Mock(side_effect=respond))

    service.release.assert_called_once_with(SCOPE, mock.ANY, "comp-new", NOW)


def test_a_release_failure_does_not_hide_the_completion_failure():
    service = reserving(ReservationOutcome.ACQUIRED)
    service.complete.side_effect = RuntimeError("throttled")
    service.release.side_effect = RuntimeError("dynamodb unavailable")

    with pytest.raises(RuntimeError, match="throttled"):
        execute(service, create=mock.Mock(side_effect=respond))


def test_a_domain_failure_that_cannot_be_stored_is_still_raised_and_releases_the_reservation():
    service = reserving(ReservationOutcome.ACQUIRED)
    service.complete.side_effect = RuntimeError("throttled")

    with pytest.raises(DomainException, match="invalid"):
        execute(service, create=mock.Mock(side_effect=DomainException("invalid")))

    service.release.assert_called_once_with(SCOPE, mock.ANY, "comp-new", NOW)


@pytest.fixture
def dynamodb_service():
    with moto.mock_aws():
        client = boto3.resource("dynamodb", region_name="eu-west-1").meta.client
        client.create_table(
            TableName="idempotency",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoDBIdempotencyService(table_name="idempotency", dynamodb_client=client)


class FlakyCompletion:
    """Fails the first completion, as a transient DynamoDB error would, then delegates."""

    def __init__(self, service):
        self._service = service
        self.completions = 0
        self.releases = []

    def reserve(self, *args):
        return self._service.reserve(*args)

    def release(self, *args):
        self.releases.append(args[2])
        return self._service.release(*args)

    def complete(self, *args):
        self.completions += 1
        if self.completions == 1:
            raise RuntimeError("throttled")
        return self._service.complete(*args)


def test_a_same_key_retry_after_a_failed_recovery_publication_recovers_the_original_id(dynamodb_service):
    created = set()
    create = mock.Mock(side_effect=lambda resource_id: created.add(resource_id) or respond(resource_id))
    # An earlier attempt created comp-new, then died before answering; its lease has since expired.
    dynamodb_service.reserve(SCOPE, idempotency.canonical_request_hash(Request(name="component")), "comp-new", NOW)
    created.add("comp-new")
    expired = NOW + timedelta(seconds=61)
    publish = mock.Mock(side_effect=[RuntimeError("eventbridge"), None])

    with pytest.raises(RuntimeError, match="eventbridge"):
        execute(
            dynamodb_service,
            create=create,
            resource_exists=created.__contains__,
            resume_existing=publish,
            resource_id="comp-retry-1",
            now=expired,
        )
    retried = execute(
        dynamodb_service,
        create=create,
        resource_exists=created.__contains__,
        resume_existing=publish,
        resource_id="comp-retry-2",
        now=expired + timedelta(seconds=1),
    )

    assert retried.body == {"id": "comp-new"}
    assert publish.call_args_list == [mock.call("comp-new"), mock.call("comp-new")]
    create.assert_not_called()
    replayed = execute(dynamodb_service, create=create, resource_id="comp-retry-3", now=expired)
    assert replayed.body == {"id": "comp-new"}


def test_a_same_key_retry_after_a_failed_completion_recovers_the_created_resource(dynamodb_service):
    service = FlakyCompletion(dynamodb_service)
    created = set()
    create = mock.Mock(side_effect=lambda resource_id: created.add(resource_id) or respond(resource_id))

    with pytest.raises(RuntimeError, match="throttled"):
        execute(service, create=create, resource_exists=created.__contains__, now=NOW)
    retried = execute(
        service,
        create=create,
        resource_exists=created.__contains__,
        resource_id="comp-retry",
        now=NOW + timedelta(seconds=1),
    )

    assert retried.body == {"id": "comp-new"}
    create.assert_called_once_with("comp-new")
    assert service.releases == ["comp-new"]
    assert execute(service, create=create, resource_id="comp-replay", now=NOW).body == {"id": "comp-new"}


def test_a_stored_validation_failure_still_replays_on_a_same_key_retry(dynamodb_service):
    create = mock.Mock(side_effect=DomainException("invalid"))

    with pytest.raises(DomainException):
        execute(dynamodb_service, create=create)
    with pytest.raises(ReplayedCreateFailure) as replayed:
        execute(dynamodb_service, create=create, resource_id="comp-retry", now=NOW + timedelta(seconds=1))

    assert replayed.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert replayed.value.retryable is False
    create.assert_called_once()
