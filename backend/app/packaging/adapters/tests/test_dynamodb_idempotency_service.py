from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.packaging.adapters.services.dynamodb_idempotency_service import DynamoDBIdempotencyService
from app.packaging.domain.ports.idempotency_service import IdempotencyScope, ReservationOutcome

TEST_TABLE_NAME = "test-table"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def scope():
    return IdempotencyScope(
        client_id="client-123",
        project_id="project-123",
        operation="create-component",
        parent_resource_id="component-123",
        key=UUID("12345678-1234-5678-1234-567812345678"),
    )


@pytest.fixture
def service(backend_app_table):
    return DynamoDBIdempotencyService(
        table_name=TEST_TABLE_NAME,
        dynamodb_client=backend_app_table.meta.client,
    )


def test_reserve_acquires_and_persists_idempotency_scope(service, backend_app_table, scope):
    reservation = service.reserve(scope, "request-hash", "component-456", NOW)

    assert reservation.outcome == ReservationOutcome.ACQUIRED
    assert reservation.resource_id == "component-456"
    assert reservation.replaced_expired is False
    item = backend_app_table.get_item(
        Key={
            "PK": "IDEMPOTENCY#client-123#project-123",
            "SK": "create-component#component-123#12345678-1234-5678-1234-567812345678",
        }
    )["Item"]
    assert item == {
        "PK": "IDEMPOTENCY#client-123#project-123",
        "SK": "create-component#component-123#12345678-1234-5678-1234-567812345678",
        "clientId": "client-123",
        "projectId": "project-123",
        "operation": "create-component",
        "parentResourceId": "component-123",
        "idempotencyKey": "12345678-1234-5678-1234-567812345678",
        "requestHash": "request-hash",
        "generatedResourceId": "component-456",
        "status": "IN_PROGRESS",
        "createdAt": 1789732800,
        "lastUpdateAt": 1789732800,
        "leaseExpiresAt": 1789732860,
        "ExpireDate": 1789819200,
    }


def test_reserve_uses_root_in_sort_key_when_scope_has_no_parent(service, backend_app_table):
    root_scope = IdempotencyScope(
        client_id="client-123",
        project_id="project-123",
        operation="create-component",
        parent_resource_id=None,
        key=UUID("12345678-1234-5678-1234-567812345678"),
    )

    service.reserve(root_scope, "request-hash", "component-456", NOW)

    item = backend_app_table.get_item(
        Key={
            "PK": "IDEMPOTENCY#client-123#project-123",
            "SK": "create-component#root#12345678-1234-5678-1234-567812345678",
        }
    )["Item"]
    assert "parentResourceId" not in item


def test_competing_first_reservations_allow_only_the_conditional_write_winner(service, scope):
    winner = service.reserve(scope, "request-hash", "component-winner", NOW)
    loser = service.reserve(scope, "request-hash", "component-loser", NOW)

    assert winner.outcome == ReservationOutcome.ACQUIRED
    assert loser.outcome == ReservationOutcome.IN_PROGRESS
    assert loser.resource_id == "component-winner"


def test_reserve_replays_identical_completed_request(service, scope):
    service.reserve(scope, "request-hash", "component-456", NOW)
    service.complete(scope, "request-hash", "component-456", 201, {"id": "component-456"}, NOW)

    reservation = service.reserve(scope, "request-hash", "unused-id", NOW + timedelta(seconds=1))

    assert reservation.outcome == ReservationOutcome.REPLAY
    assert reservation.resource_id == "component-456"
    assert reservation.response_status == 201
    assert reservation.response_body == {"id": "component-456"}


def test_reserve_returns_in_progress_while_identical_request_lease_is_active(service, scope):
    service.reserve(scope, "request-hash", "component-456", NOW)

    reservation = service.reserve(scope, "request-hash", "unused-id", NOW + timedelta(seconds=59))

    assert reservation.outcome == ReservationOutcome.IN_PROGRESS
    assert reservation.resource_id == "component-456"


def test_reserve_rejects_different_request_hash_for_active_idempotency_key(service, scope):
    service.reserve(scope, "request-hash", "component-456", NOW)

    reservation = service.reserve(scope, "different-hash", "component-789", NOW + timedelta(seconds=1))

    assert reservation.outcome == ReservationOutcome.CONFLICT
    assert reservation.resource_id == "component-456"


def test_expired_lease_takeover_has_one_recover_winner_and_subsequent_caller_is_in_progress(service, scope):
    service.reserve(scope, "request-hash", "component-456", NOW)
    stale_item = service._get_item(scope)
    assert stale_item is not None

    winner = service.reserve(scope, "request-hash", "unused-id", NOW + timedelta(seconds=60))
    loser = service.reserve(scope, "request-hash", "unused-id", NOW + timedelta(seconds=60))

    assert winner.outcome == ReservationOutcome.RECOVER
    assert winner.resource_id == "component-456"
    assert loser.outcome == ReservationOutcome.IN_PROGRESS
    assert loser.resource_id == "component-456"
    assert service._take_expired_lease(scope, "request-hash", stale_item, NOW + timedelta(seconds=60)) is False


def test_complete_persists_replay_response_and_24_hour_logical_expiry(service, backend_app_table, scope):
    service.reserve(scope, "request-hash", "component-456", NOW)

    service.complete(scope, "request-hash", "component-456", 201, {"id": "component-456"}, NOW)

    item = backend_app_table.get_item(
        Key={
            "PK": "IDEMPOTENCY#client-123#project-123",
            "SK": "create-component#component-123#12345678-1234-5678-1234-567812345678",
        }
    )["Item"]
    assert item["status"] == "COMPLETED"
    assert item["responseStatus"] == 201
    assert item["responseBody"] == {"id": "component-456"}
    assert item["lastUpdateAt"] == 1789732800
    assert item["ExpireDate"] == 1789819200


def test_logically_expired_completed_record_is_atomically_replaced_while_ttl_item_remains(service, scope):
    service.reserve(scope, "request-hash", "component-456", NOW)
    service.complete(scope, "request-hash", "component-456", 201, {"id": "component-456"}, NOW)

    reservation = service.reserve(scope, "new-hash", "component-789", NOW + timedelta(hours=24))

    assert reservation.outcome == ReservationOutcome.ACQUIRED
    assert reservation.resource_id == "component-789"
    assert reservation.replaced_expired is True
