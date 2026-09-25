from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ReservationOutcome(StrEnum):
    ACQUIRED = "ACQUIRED"
    REPLAY = "REPLAY"
    IN_PROGRESS = "IN_PROGRESS"
    CONFLICT = "CONFLICT"
    RECOVER = "RECOVER"


@dataclass(frozen=True)
class IdempotencyScope:
    client_id: str
    project_id: str
    operation: str
    parent_resource_id: str | None
    key: UUID


@dataclass(frozen=True)
class Reservation:
    outcome: ReservationOutcome
    resource_id: str
    response_status: int | None = None
    response_body: dict | None = None
    replaced_expired: bool = False


class IdempotencyService(ABC):
    @abstractmethod
    def reserve(
        self,
        scope: IdempotencyScope,
        request_hash: str,
        resource_id: str,
        now: datetime,
    ) -> Reservation: ...

    @abstractmethod
    def complete(
        self,
        scope: IdempotencyScope,
        request_hash: str,
        resource_id: str,
        response_status: int,
        response_body: dict,
        now: datetime,
    ) -> None: ...
