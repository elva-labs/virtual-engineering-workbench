import typing

from boto3.dynamodb.conditions import Attr
from mypy_boto3_dynamodb import service_resource

from app.publishing.adapters.repository import dynamo_entity_config
from app.publishing.adapters.repository.dynamo_entity_config import DBPrefix


def migrations_config():

    def portfolio_stage_in_sort_key(table: service_resource.Table):
        """001.Portfolio_Stage_In_Sort_Key

        Moves portfolios from SK AWS_ACCOUNT#<account> to AWS_ACCOUNT#<account>#STAGE#<stage>: an AWS
        account may serve several stages of one program, one portfolio each (ADR 0013). The item is
        otherwise unchanged, so its SC portfolio and name stay as they are.
        """

        for item in __get_all_portfolios(table):
            new_sort_key = dynamo_entity_config.portfolio_sort_key(item["awsAccountId"], item["stage"])
            if item["SK"] == new_sort_key:
                continue
            table.put_item(Item={**item, "SK": new_sort_key})
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

    def version_stage_in_sort_key(table: service_resource.Table):
        """002.Version_Stage_In_Sort_Key

        Moves product versions from SK VERSION#<version>#AWS_ACCOUNT#<account> to
        VERSION#<version>#AWS_ACCOUNT#<account>#STAGE#<stage>: a version is distributed per account and
        stage. The item is otherwise unchanged.
        """

        for item in __get_items(table, DBPrefix.Product, DBPrefix.Version):
            new_sort_key = dynamo_entity_config.version_sort_key(item["versionId"], item["awsAccountId"], item["stage"])
            if item["SK"] == new_sort_key:
                continue
            table.put_item(Item={**item, "SK": new_sort_key})
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

    return [
        (
            "001.Portfolio_Stage_In_Sort_Key",
            portfolio_stage_in_sort_key,
        ),
        (
            "002.Version_Stage_In_Sort_Key",
            version_stage_in_sort_key,
        ),
    ]


def __get_all_portfolios(table: service_resource.Table) -> typing.Iterator[dict]:
    return __get_items(table, DBPrefix.Technology, DBPrefix.AwsAccount)


def __get_items(table: service_resource.Table, pk_prefix: str, sk_prefix: str) -> typing.Iterator[dict]:
    scan_kwargs = {
        "FilterExpression": Attr("PK").begins_with(f"{pk_prefix}#") & Attr("SK").begins_with(f"{sk_prefix}#")
    }
    while True:
        page = table.scan(**scan_kwargs)
        for item in page.get("Items", []):
            yield item
        if "LastEvaluatedKey" not in page:
            return
        scan_kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
