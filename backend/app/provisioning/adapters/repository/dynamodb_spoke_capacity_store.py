"""Spoke capacity snapshots and quota increase requests in the provisioning table.

One item per spoke account and region (replaced by every collector run) and one per increase
request. Both live under a fixed partition key, so the admin overview is a single query, and the
launch check a single GetItem.
"""

from boto3.dynamodb.conditions import Key

from app.provisioning.domain.model import spoke_capacity

SNAPSHOT_PK = "SPOKE_CAPACITY"
REQUEST_PK = "QUOTA_REQUEST"


def _snapshot_sk(aws_account_id: str, region: str) -> str:
    return f"ACCOUNT#{aws_account_id}#REGION#{region}"


def _request_sk(request: spoke_capacity.QuotaIncreaseRequest) -> str:
    return f"ACCOUNT#{request.awsAccountId}#REGION#{request.region}#QUOTA#{request.quotaCode}#{request.requestedAt}"


class DynamoDBSpokeCapacityStore:
    def __init__(self, table):
        """table: a boto3 dynamodb Table resource."""
        self._table = table

    def put_snapshot(self, snapshot: spoke_capacity.SpokeCapacity) -> None:
        self._table.put_item(
            Item={
                "PK": SNAPSHOT_PK,
                "SK": _snapshot_sk(snapshot.awsAccountId, snapshot.region),
                "capacity": snapshot.model_dump_json(),
            }
        )

    def get_snapshot(self, aws_account_id: str, region: str) -> spoke_capacity.SpokeCapacity | None:
        item = self._table.get_item(Key={"PK": SNAPSHOT_PK, "SK": _snapshot_sk(aws_account_id, region)}).get("Item")
        return spoke_capacity.SpokeCapacity.model_validate_json(item["capacity"]) if item else None

    def list_snapshots(self) -> list[spoke_capacity.SpokeCapacity]:
        return [
            spoke_capacity.SpokeCapacity.model_validate_json(item["capacity"])
            for item in self._query(SNAPSHOT_PK, None)
        ]

    def put_request(self, request: spoke_capacity.QuotaIncreaseRequest) -> None:
        self._table.put_item(Item={"PK": REQUEST_PK, "SK": _request_sk(request), "request": request.model_dump_json()})

    def list_requests(self, aws_account_id: str | None = None) -> list[spoke_capacity.QuotaIncreaseRequest]:
        prefix = f"ACCOUNT#{aws_account_id}#" if aws_account_id else None
        requests = [
            spoke_capacity.QuotaIncreaseRequest.model_validate_json(item["request"])
            for item in self._query(REQUEST_PK, prefix)
        ]
        return sorted(requests, key=lambda r: r.requestedAt, reverse=True)

    def _query(self, pk: str, sk_prefix: str | None) -> list[dict]:
        condition = Key("PK").eq(pk)
        if sk_prefix:
            condition = condition & Key("SK").begins_with(sk_prefix)
        items, kwargs = [], {"KeyConditionExpression": condition}
        while True:
            page = self._table.query(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
