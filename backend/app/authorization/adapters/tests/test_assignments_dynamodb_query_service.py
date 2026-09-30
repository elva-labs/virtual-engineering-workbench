import assertpy
import boto3
import moto
import pytest

from app.authorization.adapters.query_services import assignments_dynamodb_query_service
from app.authorization.adapters.repository import dynamo_entity_config
from app.authorization.domain.read_models import project_assignment
from app.shared.adapters.unit_of_work_v2 import dynamodb_unit_of_work, unit_of_work


@pytest.fixture
def gsi_name_inverted_pk():
    return "GSI1"


@pytest.fixture
def test_table_name():
    return "test_table"


@pytest.fixture
def mock_dynamodb():
    with moto.mock_aws():
        yield boto3.resource("dynamodb", region_name="eu-central-1")


@pytest.fixture()
def backend_app_dynamodb_table(mock_dynamodb, test_table_name, gsi_name_inverted_pk):
    table = mock_dynamodb.create_table(
        TableName=test_table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": gsi_name_inverted_pk,
                "KeySchema": [
                    {"AttributeName": "SK", "KeyType": "HASH"},
                    {"AttributeName": "PK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )

    table.meta.client.get_waiter("table_exists").wait(TableName=test_table_name)
    return table


@pytest.fixture()
def ddb_uow(backend_app_dynamodb_table, test_table_name, mock_logger):
    return dynamodb_unit_of_work.DynamoDBUnitOfWork(
        table_name=test_table_name,
        dynamodb_client=backend_app_dynamodb_table.meta.client,
        repo_factories=dynamo_entity_config.EntityConfigurator(table_name=test_table_name).repo_factories(),
        logger=mock_logger,
    )


def test_get_user_assignments_returns_all_users_assignments(
    ddb_uow: unit_of_work.UnitOfWork,
    test_table_name: str,
    backend_app_dynamodb_table,
    gsi_name_inverted_pk,
):
    # ARRANGE
    with ddb_uow as uow:
        repo = uow.get_repository(project_assignment.AssignmentPrimaryKey, project_assignment.Assignment)
        for i in range(6):
            repo.add(
                project_assignment.Assignment(
                    userId=f"user-{i % 2}",
                    projectId=f"proj-{i % 3}",
                    roles=[project_assignment.Role.PLATFORM_USER],
                    userEmail="",
                    activeDirectoryGroups=[{"a": "b"}],
                )
            )
        uow.commit()

    qs = assignments_dynamodb_query_service.AssignmentsDynamoDBQueryService(
        table_name=test_table_name,
        dynamodb_client=backend_app_dynamodb_table.meta.client,
        gsi_inverted_pk=gsi_name_inverted_pk,
    )

    # ACT
    assignments = qs.get_user_assignments(user_id="user-0")

    # ASSERT
    assertpy.assert_that(assignments).is_length(3)
    assertpy.assert_that(assignments).contains_only(
        project_assignment.Assignment(
            userId="user-0",
            projectId="proj-0",
            roles=[project_assignment.Role.PLATFORM_USER],
            userEmail="",
            activeDirectoryGroups=[{"a": "b"}],
        ),
        project_assignment.Assignment(
            userId="user-0",
            projectId="proj-2",
            roles=[project_assignment.Role.PLATFORM_USER],
            userEmail="",
            activeDirectoryGroups=[{"a": "b"}],
        ),
        project_assignment.Assignment(
            userId="user-0",
            projectId="proj-1",
            roles=[project_assignment.Role.PLATFORM_USER],
            userEmail="",
            activeDirectoryGroups=[{"a": "b"}],
        ),
    )


def test_get_project_assignments_returns_all_project_assignments(
    ddb_uow: unit_of_work.UnitOfWork,
    test_table_name: str,
    backend_app_dynamodb_table,
    gsi_name_inverted_pk,
):
    # ARRANGE
    with ddb_uow as uow:
        repo = uow.get_repository(project_assignment.AssignmentPrimaryKey, project_assignment.Assignment)
        for i in range(6):
            repo.add(
                project_assignment.Assignment(
                    userId=f"user-{i % 2}",
                    projectId=f"proj-{i % 3}",
                    roles=[project_assignment.Role.PLATFORM_USER],
                    userEmail="",
                    activeDirectoryGroups=[{"a": "b"}],
                )
            )
        uow.commit()

    qs = assignments_dynamodb_query_service.AssignmentsDynamoDBQueryService(
        table_name=test_table_name,
        dynamodb_client=backend_app_dynamodb_table.meta.client,
        gsi_inverted_pk=gsi_name_inverted_pk,
    )

    # ACT
    assignments = qs.get_project_assignments(project_id="proj-0")

    # ASSERT
    assertpy.assert_that(assignments).is_length(2)
    assertpy.assert_that(assignments).contains_only(
        project_assignment.Assignment(
            userId="user-0",
            projectId="proj-0",
            roles=[project_assignment.Role.PLATFORM_USER],
            userEmail="",
            activeDirectoryGroups=[{"a": "b"}],
        ),
        project_assignment.Assignment(
            userId="user-1",
            projectId="proj-0",
            roles=[project_assignment.Role.PLATFORM_USER],
            userEmail="",
            activeDirectoryGroups=[{"a": "b"}],
        ),
    )


def test_group_projection_replay_and_tombstone(
    ddb_uow, test_table_name, backend_app_dynamodb_table, gsi_name_inverted_pk
):
    from app.authorization.domain.integration_events.projects.project_group_assignment_changed import (
        ProjectGroupAssignmentChanged,
    )
    from app.authorization.domain.integration_event_handlers.projects.project_group_assignment_changed_handler import (
        handle,
    )
    from app.authorization.domain.read_models.project_group_assignment import (
        GroupAssignment,
        GroupAssignmentPrimaryKey,
    )

    group = "12345678-1234-1234-1234-123456789abc"
    query = assignments_dynamodb_query_service.AssignmentsDynamoDBQueryService(
        table_name=test_table_name,
        dynamodb_client=backend_app_dynamodb_table.meta.client,
        gsi_inverted_pk=gsi_name_inverted_pk,
    )

    def event(version, roles, deleted=False):
        return ProjectGroupAssignmentChanged(
            projectId="p",
            groupId=group,
            roles=roles,
            version=version,
            isDeleted=deleted,
        )

    handle(event(1, ["PLATFORM_USER"]), ddb_uow)
    assert query.get_group_assignments([group])[0].roles == ["PLATFORM_USER"]
    handle(event(2, ["ADMIN"]), ddb_uow)
    handle(event(1, ["PLATFORM_USER"]), ddb_uow)
    assert query.get_group_assignments([group, group])[0].roles == ["ADMIN"]
    handle(event(3, [], True), ddb_uow)
    handle(event(2, ["ADMIN"]), ddb_uow)
    handle(event(3, [], True), ddb_uow)
    assert query.get_group_assignments([group]) == []
    with ddb_uow:
        record = ddb_uow.get_repository(GroupAssignmentPrimaryKey, GroupAssignment).get(
            GroupAssignmentPrimaryKey(projectId="p", groupId=group)
        )
        assert record.isDeleted and record.version == 3
