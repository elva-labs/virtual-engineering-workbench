"""Compatibility export for Packaging's shared DynamoDB reservation adapter."""

from app.shared.adapters.idempotency.dynamodb_idempotency_service import (
    COMPLETED_RECORD_DURATION,
    LEASE_DURATION,
    DynamoDBDocumentClient,
    DynamoDBIdempotencyService,
)

__all__ = [
    "COMPLETED_RECORD_DURATION",
    "LEASE_DURATION",
    "DynamoDBDocumentClient",
    "DynamoDBIdempotencyService",
]
