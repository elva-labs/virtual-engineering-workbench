"""Compatibility exports for the shared idempotency reservation contract."""

from app.shared.domain.ports.idempotency_service import (
    IdempotencyScope,
    IdempotencyService,
    Reservation,
    ReservationOutcome,
)

__all__ = ["IdempotencyScope", "IdempotencyService", "Reservation", "ReservationOutcome"]
