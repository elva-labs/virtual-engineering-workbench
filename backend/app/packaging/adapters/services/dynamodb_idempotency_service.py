from datetime import datetime, timedelta

from mypy_boto3_dynamodb import client

from app.packaging.domain.ports import idempotency_service

LEASE_DURATION = timedelta(seconds=60)
COMPLETED_RECORD_DURATION = timedelta(hours=24)


class DynamoDBIdempotencyService(idempotency_service.IdempotencyService):
    def __init__(self, table_name: str, dynamodb_client: client.DynamoDBClient):
        self._table_name = table_name
        self._client = dynamodb_client

    def reserve(
        self,
        scope: idempotency_service.IdempotencyScope,
        request_hash: str,
        resource_id: str,
        now: datetime,
    ) -> idempotency_service.Reservation:
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=self._new_item(scope, request_hash, resource_id, now),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return self._classify_existing(scope, request_hash, resource_id, now)

        return idempotency_service.Reservation(idempotency_service.ReservationOutcome.ACQUIRED, resource_id)

    def complete(
        self,
        scope: idempotency_service.IdempotencyScope,
        request_hash: str,
        resource_id: str,
        response_status: int,
        response_body: dict,
        now: datetime,
    ) -> None:
        self._client.update_item(
            TableName=self._table_name,
            Key=self._key(scope),
            UpdateExpression=(
                "SET #status = :completed, responseStatus = :status, "
                "responseBody = :body, lastUpdateAt = :now, ExpireDate = :expiry"
            ),
            ConditionExpression="requestHash = :hash AND generatedResourceId = :resource",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":completed": "COMPLETED",
                ":status": response_status,
                ":body": response_body,
                ":now": self._timestamp(now),
                ":expiry": self._timestamp(now + COMPLETED_RECORD_DURATION),
                ":hash": request_hash,
                ":resource": resource_id,
            },
        )

    def _classify_existing(
        self,
        scope: idempotency_service.IdempotencyScope,
        request_hash: str,
        resource_id: str,
        now: datetime,
    ) -> idempotency_service.Reservation:
        item = self._get_item(scope)
        if item is None:
            return self.reserve(scope, request_hash, resource_id, now)

        if item["status"] == "COMPLETED" and self._is_logically_expired(item, now):
            if self._replace_expired_completed(scope, request_hash, resource_id, now, item):
                return idempotency_service.Reservation(
                    idempotency_service.ReservationOutcome.ACQUIRED,
                    resource_id,
                    replaced_expired=True,
                )
            return self._classify_existing(scope, request_hash, resource_id, now)

        if item["requestHash"] != request_hash:
            return idempotency_service.Reservation(
                idempotency_service.ReservationOutcome.CONFLICT,
                item["generatedResourceId"],
            )

        if item["status"] == "COMPLETED":
            return idempotency_service.Reservation(
                idempotency_service.ReservationOutcome.REPLAY,
                item["generatedResourceId"],
                response_status=int(item["responseStatus"]),
                response_body=item["responseBody"],
            )

        if self._lease_expired(item, now):
            if self._take_expired_lease(scope, request_hash, item, now):
                return idempotency_service.Reservation(
                    idempotency_service.ReservationOutcome.RECOVER,
                    item["generatedResourceId"],
                )
            return self._classify_existing(scope, request_hash, resource_id, now)

        return idempotency_service.Reservation(
            idempotency_service.ReservationOutcome.IN_PROGRESS,
            item["generatedResourceId"],
        )

    def _replace_expired_completed(
        self,
        scope: idempotency_service.IdempotencyScope,
        request_hash: str,
        resource_id: str,
        now: datetime,
        item: dict,
    ) -> bool:
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=self._new_item(scope, request_hash, resource_id, now),
                ConditionExpression="#status = :completed AND ExpireDate = :observed_expiry",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":completed": "COMPLETED", ":observed_expiry": item["ExpireDate"]},
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def _take_expired_lease(
        self,
        scope: idempotency_service.IdempotencyScope,
        request_hash: str,
        item: dict,
        now: datetime,
    ) -> bool:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=self._key(scope),
                UpdateExpression="SET leaseExpiresAt = :new_lease, lastUpdateAt = :now",
                ConditionExpression="#status = :in_progress AND requestHash = :hash AND leaseExpiresAt = :observed_lease",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":new_lease": self._timestamp(now + LEASE_DURATION),
                    ":now": self._timestamp(now),
                    ":in_progress": "IN_PROGRESS",
                    ":hash": request_hash,
                    ":observed_lease": item["leaseExpiresAt"],
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def _get_item(self, scope: idempotency_service.IdempotencyScope) -> dict | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=self._key(scope),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return item

    def _new_item(
        self,
        scope: idempotency_service.IdempotencyScope,
        request_hash: str,
        resource_id: str,
        now: datetime,
    ) -> dict:
        timestamp = self._timestamp(now)
        item = {
            **self._key(scope),
            "clientId": scope.client_id,
            "projectId": scope.project_id,
            "operation": scope.operation,
            "idempotencyKey": str(scope.key),
            "requestHash": request_hash,
            "generatedResourceId": resource_id,
            "status": "IN_PROGRESS",
            "createdAt": timestamp,
            "lastUpdateAt": timestamp,
            "leaseExpiresAt": self._timestamp(now + LEASE_DURATION),
            "ExpireDate": self._timestamp(now + COMPLETED_RECORD_DURATION),
        }
        if scope.parent_resource_id is not None:
            item["parentResourceId"] = scope.parent_resource_id
        return item

    @staticmethod
    def _key(scope: idempotency_service.IdempotencyScope) -> dict:
        parent = scope.parent_resource_id or "root"
        return {
            "PK": f"IDEMPOTENCY#{scope.client_id}#{scope.project_id}",
            "SK": f"{scope.operation}#{parent}#{scope.key}",
        }

    @staticmethod
    def _timestamp(value: datetime) -> int:
        return int(value.timestamp())

    def _is_logically_expired(self, item: dict, now: datetime) -> bool:
        return int(item["ExpireDate"]) <= self._timestamp(now)

    def _lease_expired(self, item: dict, now: datetime) -> bool:
        return int(item["leaseExpiresAt"]) <= self._timestamp(now)
