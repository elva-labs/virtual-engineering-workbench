import boto3
import moto
import pytest

from app.publishing.adapters.repository import dynamo_entity_migrations

TEST_TABLE_NAME = "test-table"


@pytest.fixture
def table():
    with moto.mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="eu-north-1")
        yield dynamodb.create_table(
            TableName=TEST_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )


def _portfolio(sort_key: str, stage: str = "DEV") -> dict:
    return {
        "PK": "TECHNOLOGY#tech-12345",
        "SK": sort_key,
        "entity": "PORTFOLIO",
        "portfolioId": "port-12345",
        "technologyId": "tech-12345",
        "awsAccountId": "123456789012",
        "stage": stage,
        "scPortfolioId": "port-sc12345",
        "scPortfolioName": "portfolio-tech-12345-123456789012",
    }


def _run(table, name: str = "001.Portfolio_Stage_In_Sort_Key"):
    scripts = dict(dynamo_entity_migrations.migrations_config())
    scripts[name](table)


def test_portfolio_moves_to_the_stage_sort_key(table):
    table.put_item(Item=_portfolio("AWS_ACCOUNT#123456789012"))
    table.put_item(Item={"PK": "PRODUCT#prod-1", "SK": "VERSION#vers-1#AWS_ACCOUNT#123456789012#STAGE#DEV"})

    _run(table)

    items = {item["SK"]: item for item in table.scan()["Items"]}
    assert "AWS_ACCOUNT#123456789012" not in items
    assert items["AWS_ACCOUNT#123456789012#STAGE#DEV"] == _portfolio("AWS_ACCOUNT#123456789012#STAGE#DEV")
    # Other entities are left alone.
    assert "VERSION#vers-1#AWS_ACCOUNT#123456789012#STAGE#DEV" in items


def test_portfolio_migration_is_idempotent(table):
    table.put_item(Item=_portfolio("AWS_ACCOUNT#123456789012#STAGE#PROD", stage="PROD"))

    _run(table)
    _run(table)

    assert [item["SK"] for item in table.scan()["Items"]] == ["AWS_ACCOUNT#123456789012#STAGE#PROD"]


def _version(sort_key: str, stage: str = "PROD") -> dict:
    return {
        "PK": "PRODUCT#prod-1",
        "SK": sort_key,
        "entity": "VERSION",
        "productId": "prod-1",
        "versionId": "vers-1",
        "awsAccountId": "123456789012",
        "stage": stage,
    }


def test_version_moves_to_the_stage_sort_key(table):
    table.put_item(Item=_version("VERSION#vers-1#AWS_ACCOUNT#123456789012"))
    table.put_item(Item=_portfolio("AWS_ACCOUNT#123456789012#STAGE#DEV"))

    _run(table, "002.Version_Stage_In_Sort_Key")

    keys = sorted(item["SK"] for item in table.scan()["Items"])
    assert keys == ["AWS_ACCOUNT#123456789012#STAGE#DEV", "VERSION#vers-1#AWS_ACCOUNT#123456789012#STAGE#PROD"]


def test_version_migration_is_idempotent(table):
    table.put_item(Item=_version("VERSION#vers-1#AWS_ACCOUNT#123456789012#STAGE#PROD"))

    _run(table, "002.Version_Stage_In_Sort_Key")
    _run(table, "002.Version_Stage_In_Sort_Key")

    items = table.scan()["Items"]
    assert [item["SK"] for item in items] == ["VERSION#vers-1#AWS_ACCOUNT#123456789012#STAGE#PROD"]
