from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from app.projects.adapters.query_services.dynamodb_query_service import DynamoDBProjectsQueryService
from app.projects.domain.model import project_assignment
from app.projects.domain.project_group_assignment_service import ProjectGroupAssignmentService
from app.projects.domain.project_lifecycle_service import ProjectLifecycleService
from app.shared.adapters.unit_of_work_v2.repository_exception import RepositoryException


def query_service(mock_dynamodb, test_table_name):
    return DynamoDBProjectsQueryService(
        table_name=test_table_name,
        dynamodb_client=mock_dynamodb.meta.client,
        gsi_inverted_primary_key="gsi_inverted_primary_key",
        gsi_aws_accounts="gsi_aws_accounts",
        gsi_entities="gsi_entities",
    )


def test_create_recovers_lost_response_and_assigns_creator(mock_ddb_repo, mock_dynamodb, test_table_name):
    query = query_service(mock_dynamodb, test_table_name)
    events = Mock()
    service = ProjectLifecycleService(mock_ddb_repo, query, events)
    key = str(uuid4())
    with patch(
        "app.projects.domain.model.project.generate_project_id",
        return_value="proj-stable",
    ):
        first = service.create("client-1", key, "Name", None, True)
        second = service.create("client-1", key, "Name", None, True)
    assert first == second == "proj-stable"
    assert query.get_project_by_id(first).isActive
    assert query.get_service_client_assignment(first, "client-1").status == "ACTIVE"
    events.publish.assert_called_once()
    with pytest.raises(ValueError, match="different request"):
        service.create("client-1", key, "Changed", None, True)


def test_group_upsert_delete_and_index_lookup(mock_ddb_repo, mock_dynamodb, test_table_name):
    query = query_service(mock_dynamodb, test_table_name)
    events = Mock()
    lifecycle = ProjectLifecycleService(mock_ddb_repo, query, events)
    with patch(
        "app.projects.domain.model.project.generate_project_id",
        return_value="proj-group",
    ):
        lifecycle.create("client-1", str(uuid4()), "Group project", None, True)
    groups = ProjectGroupAssignmentService(mock_ddb_repo, query, events)
    group_id = str(uuid4())
    first = groups.put("proj-group", group_id.upper(), [project_assignment.Role.PLATFORM_USER])
    assert first.version == 1
    assert len(query.get_group_assignments([group_id])) == 1
    assert len(query.list_project_group_assignments("proj-group")) == 1
    repeated = groups.put("proj-group", group_id, [project_assignment.Role.PLATFORM_USER])
    assert repeated.version == 1
    groups.delete("proj-group", group_id)
    groups.delete("proj-group", group_id)
    assert query.get_group_assignments([group_id]) == []
    assert query.list_project_group_assignments("proj-group") == []
    tombstone = query.get_project_group_assignment("proj-group", group_id)
    assert tombstone.isDeleted and tombstone.version == 2
    restored = groups.put("proj-group", group_id, [project_assignment.Role.ADMIN])
    assert restored.version == 3 and not restored.isDeleted
    with pytest.raises(ValueError):
        groups.put("proj-group", group_id, ["UNKNOWN"])


def test_group_name_is_stored_kept_and_changed(mock_ddb_repo, mock_dynamodb, test_table_name):
    """The group's display name rides with the binding; omitting it keeps the stored one."""
    query = query_service(mock_dynamodb, test_table_name)
    events = Mock()
    lifecycle = ProjectLifecycleService(mock_ddb_repo, query, events)
    with patch(
        "app.projects.domain.model.project.generate_project_id",
        return_value="proj-names",
    ):
        lifecycle.create("client-1", str(uuid4()), "Names project", None, True)
    groups = ProjectGroupAssignmentService(mock_ddb_repo, query, events)
    group_id = str(uuid4())
    roles = [project_assignment.Role.PLATFORM_USER]
    named = groups.put("proj-names", group_id, roles, "vew-names-users")
    assert named.groupName == "vew-names-users" and named.version == 1
    assert query.get_project_group_assignment("proj-names", group_id).groupName == "vew-names-users"
    kept = groups.put("proj-names", group_id, roles)
    assert kept.version == 1 and kept.groupName == "vew-names-users"
    same = groups.put("proj-names", group_id, roles, "vew-names-users")
    assert same.version == 1
    renamed = groups.put("proj-names", group_id, roles, "vew-names-renamed")
    assert renamed.version == 2 and renamed.groupName == "vew-names-renamed"


def test_create_replay_does_not_restore_revoked_creator(
    mock_ddb_repo, mock_dynamodb, test_table_name, backend_app_dynamodb_table
):
    query = query_service(mock_dynamodb, test_table_name)
    service = ProjectLifecycleService(mock_ddb_repo, query, Mock())
    key = str(uuid4())
    with patch(
        "app.projects.domain.model.project.generate_project_id",
        return_value="proj-revoked",
    ):
        service.create("client-1", key, "Name", None, True)
    item = backend_app_dynamodb_table.get_item(Key={"PK": "CLIENT#client-1", "SK": "PROJECT#proj-revoked"})["Item"]
    item["status"] = "REVOKED"
    backend_app_dynamodb_table.put_item(Item=item)
    assert service.create("client-1", key, "Name", None, True) == "proj-revoked"
    assert query.get_service_client_assignment("proj-revoked", "client-1").status == "REVOKED"


def test_stale_group_update_cannot_reuse_version(mock_ddb_repo, mock_dynamodb, test_table_name):
    query = query_service(mock_dynamodb, test_table_name)
    lifecycle = ProjectLifecycleService(mock_ddb_repo, query, Mock())
    with patch(
        "app.projects.domain.model.project.generate_project_id",
        return_value="proj-race",
    ):
        lifecycle.create("client-1", str(uuid4()), "Name", None, True)
    groups = ProjectGroupAssignmentService(mock_ddb_repo, query, Mock())
    group_id = str(uuid4())
    groups.put("proj-race", group_id, ["PLATFORM_USER"])
    stale = query.get_project_group_assignment("proj-race", group_id)
    groups.put("proj-race", group_id, ["ADMIN"])
    stale_query = Mock(wraps=query)
    stale_query.get_project_group_assignment.return_value = stale
    with pytest.raises(RepositoryException):
        ProjectGroupAssignmentService(mock_ddb_repo, stale_query, Mock()).put("proj-race", group_id, ["POWER_USER"])
    current = query.get_project_group_assignment("proj-race", group_id)
    assert current.version == 2 and current.roles == ["ADMIN"]


def test_set_management_marks_unmarks_and_keeps_it_on_update(mock_ddb_repo, mock_dynamodb, test_table_name):
    query = query_service(mock_dynamodb, test_table_name)
    events = Mock()
    service = ProjectLifecycleService(mock_ddb_repo, query, events)
    with patch(
        "app.projects.domain.model.project.generate_project_id",
        return_value="proj-managed",
    ):
        service.create("client-1", str(uuid4()), "Managed", None, True)
    events.reset_mock()

    service.set_management("proj-managed", "terraform", "org/config programs/managed")
    stored = query.get_project_by_id("proj-managed")
    assert (stored.managedBy, stored.managedSource) == ("terraform", "org/config programs/managed")
    published = events.publish.call_args.args[0]
    assert (published.managed_by, published.managed_source) == ("terraform", "org/config programs/managed")

    # Unchanged: nothing is published.
    service.set_management("proj-managed", "terraform", "org/config programs/managed")
    assert events.publish.call_count == 1

    # An update keeps the mark and carries it in its event.
    service.update("proj-managed", "Renamed", None, True)
    assert query.get_project_by_id("proj-managed").managedBy == "terraform"
    assert events.publish.call_args.args[0].managed_by == "terraform"

    service.set_management("proj-managed", None, "ignored")
    stored = query.get_project_by_id("proj-managed")
    assert (stored.managedBy, stored.managedSource) == (None, None)
    with pytest.raises(KeyError):
        service.set_management("proj-missing", "terraform", "x")


def test_set_workbench_lifecycle_stores_replaces_and_resets(mock_ddb_repo, mock_dynamodb, test_table_name):
    from app.projects.domain.model import workbench_lifecycle

    query = query_service(mock_dynamodb, test_table_name)
    events = Mock()
    service = ProjectLifecycleService(mock_ddb_repo, query, events)
    with patch(
        "app.projects.domain.model.project.generate_project_id",
        return_value="proj-lifecycle",
    ):
        service.create("client-1", str(uuid4()), "Lifecycle", None, True)

    policy = workbench_lifecycle.WorkbenchLifecycle(
        weekendStop=False, allowUserIdleTimeout=True, userIdleTimeoutMinMinutes=10, userIdleTimeoutMaxMinutes=240
    )
    service.set_workbench_lifecycle("proj-lifecycle", policy)
    assert query.get_project_by_id("proj-lifecycle").workbenchLifecycle == policy

    service.set_workbench_lifecycle("proj-lifecycle", None)
    assert query.get_project_by_id("proj-lifecycle").workbenchLifecycle is None
    with pytest.raises(KeyError):
        service.set_workbench_lifecycle("proj-missing", policy)
