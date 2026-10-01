"""Project settings (remote support) in the Authorization BC table."""

import assertpy
import boto3
import moto
import pytest

from app.authorization.adapters.query_services import assignments_dynamodb_query_service
from app.authorization.adapters.repository import dynamo_entity_config
from app.authorization.domain.read_models import project_assignment, project_settings
from app.shared.adapters.unit_of_work_v2 import dynamodb_unit_of_work

TABLE = "test_table"
GSI = "GSI1"


@pytest.fixture
def table():
    with moto.mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="eu-central-1")
        table = dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[
                {
                    "IndexName": GSI,
                    "KeySchema": [
                        {"AttributeName": "SK", "KeyType": "HASH"},
                        {"AttributeName": "PK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE)
        yield table


@pytest.fixture
def uow(table, mock_logger):
    return dynamodb_unit_of_work.DynamoDBUnitOfWork(
        table_name=TABLE,
        dynamodb_client=table.meta.client,
        repo_factories=dynamo_entity_config.EntityConfigurator(table_name=TABLE).repo_factories(),
        logger=mock_logger,
    )


@pytest.fixture
def qs(table):
    return assignments_dynamodb_query_service.AssignmentsDynamoDBQueryService(
        table_name=TABLE, dynamodb_client=table.meta.client, gsi_inverted_pk=GSI
    )


def test_get_project_settings_defaults_to_remote_support_enabled(qs):
    assertpy.assert_that(qs.get_project_settings(project_id="proj-0")).is_equal_to(
        project_settings.ProjectSettings(projectId="proj-0", remoteSupportEnabled=True)
    )


def test_get_project_settings_returns_stored_settings_and_does_not_leak_into_assignments(uow, qs):
    # ARRANGE
    with uow:
        uow.get_repository(project_settings.ProjectSettingsPrimaryKey, project_settings.ProjectSettings).add(
            project_settings.ProjectSettings(projectId="proj-0", remoteSupportEnabled=False)
        )
        uow.get_repository(project_assignment.AssignmentPrimaryKey, project_assignment.Assignment).add(
            project_assignment.Assignment(userId="user-0", projectId="proj-0", roles=[project_assignment.Role.SUPPORT])
        )
        uow.commit()

    # ACT / ASSERT
    assertpy.assert_that(qs.get_project_settings(project_id="proj-0").remoteSupportEnabled).is_false()
    assertpy.assert_that(qs.get_project_assignments(project_id="proj-0")).is_length(1)
    assertpy.assert_that(qs.get_user_assignments(user_id="user-0")).is_length(1)
