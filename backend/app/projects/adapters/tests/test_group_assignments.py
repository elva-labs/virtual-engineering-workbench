import logging
from unittest.mock import Mock

import pytest

from app.projects.adapters.query_services.dynamodb_query_service import DynamoDBProjectsQueryService
from app.projects.adapters.repository.dynamo_entity_config import EntityConfigurator
from app.projects.domain.model import project, project_assignment
from app.projects.domain.project_group_assignment_service import ProjectGroupAssignmentService
from app.shared.adapters.unit_of_work_v2.dynamodb_unit_of_work import DynamoDBUnitOfWork


@pytest.fixture
def services(mock_dynamodb, test_table_name, gsi_name, gsi_aws_accounts, gsi_entities):
    query = DynamoDBProjectsQueryService(
        test_table_name, mock_dynamodb.meta.client, gsi_name, gsi_aws_accounts, gsi_entities
    )
    uow = DynamoDBUnitOfWork(
        table_name=test_table_name,
        dynamodb_client=mock_dynamodb.meta.client,
        repo_factories=EntityConfigurator(test_table_name).repo_factories(),
        logger=logging.getLogger(__name__),
    )
    with uow:
        uow.get_repository(project.ProjectPrimaryKey, project.Project).add(
            project.Project(projectId="p1", projectName="One", isActive=True)
        )
        uow.get_repository(project_assignment.AssignmentPrimaryKey, project_assignment.Assignment).add(
            project_assignment.Assignment(projectId="p1", userId="USER", roles=["ADMIN"])
        )
        uow.commit()
    bus = Mock()
    return ProjectGroupAssignmentService(uow, query, bus), query, bus


def test_binding_create_update_revoke_restore_preserves_direct_users(services, backend_app_dynamodb_table):
    service, query, bus = services
    group = "engineering/team:東京"
    created = service.put("p1", group, ["PLATFORM_USER", "PLATFORM_USER"], "Engineering")
    assert (created.roles, created.version) == (["PLATFORM_USER"], 1)
    repeated = service.put("p1", group, ["PLATFORM_USER"], "Engineering")
    assert repeated.version == 1
    updated = service.put("p1", group, ["PROGRAM_OWNER"], None)
    assert (updated.roles, updated.version, updated.groupName) == (["PROGRAM_OWNER"], 2, None)
    assert query.get_group_assignments([group])[0].roles == ["PROGRAM_OWNER"]
    assert query.list_project_group_assignments("p1")[0].groupId == group
    service.delete("p1", group)
    service.delete("p1", group)
    assert query.get_group_assignments([group]) == []
    assert query.list_project_group_assignments("p1") == []
    tombstone = query.list_project_group_assignments("p1", include_deleted=True)[0]
    assert tombstone.isDeleted and tombstone.version == 3
    restored = service.put("p1", group, ["BETA_USER"], "New label")
    assert restored.version == 4 and not restored.isDeleted
    assert query.get_group_assignments([group, group])[0].roles == ["BETA_USER"]
    assert query.get_user_assignment("p1", "USER").roles == ["ADMIN"]
    assert [call.args[0].version for call in bus.publish.call_args_list] == [1, 1, 2, 3, 3, 4]
    items = backend_app_dynamodb_table.scan()["Items"]
    assert len([item for item in items if item["PK"].startswith("USER#")]) == 1


def test_bindings_require_existing_project_and_existing_roles(services):
    service, _, bus = services
    with pytest.raises(ValueError, match="Project not found"):
        service.put("missing", "team", ["ADMIN"])
    with pytest.raises(ValueError):
        service.put("p1", "team", ["SUPPORT"])
    service.delete("p1", "missing")
    bus.publish.assert_not_called()


def test_empty_roles_grant_nothing(services):
    service, query, _ = services
    service.put("p1", "empty", [])
    assert query.get_group_assignments(["empty"])[0].roles == []
