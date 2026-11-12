import json
import logging
from unittest.mock import Mock

import pytest

from app.projects.adapters.tests.test_group_assignments import services  # noqa: F401
from app.projects.domain.command_handlers.users import (
    assign_user_command_handler,
    reassign_user_command_handler,
    unassign_user_command_handler,
)
from app.projects.domain.commands.users import assign_user_command, reassign_user_command, unassign_user_command
from app.projects.domain.events.users import user_assigned, user_reassigned, user_unassigned
from app.projects.domain.group_membership_service import GroupMembershipService
from app.projects.domain.model import project, project_assignment
from app.projects.domain.value_objects import project_id_value_object, user_id_value_object, user_role_value_object
from app.shared.adapters.message_bus.in_memory_command_bus import InMemoryCommandBus
from app.shared.adapters.unit_of_work_v2.repository_exception import ConditionalCheckFailedException


def profile(groups, provider="CorporateLogin"):
    return {"identities": json.dumps([{"providerName": provider}]), "custom:oidc_groups": json.dumps(groups)}


@pytest.fixture
def memberships(services, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", "CorporateLogin")
    bindings, query, events = services
    uow = bindings._uow
    directory = Mock()
    directory.get_user_email.return_value = "member@example.com"
    enrolments = Mock()
    enrolments.list_enrolments_by_user.return_value = ([], None)
    commands = InMemoryCommandBus(logging.getLogger(__name__))
    commands.register_handler(
        assign_user_command.AssignUserCommand,
        lambda cmd: assign_user_command_handler.handle_assign_user_command(cmd, uow, query, events, directory),
    )
    commands.register_handler(
        reassign_user_command.ReAssignUserCommand,
        lambda cmd: reassign_user_command_handler.handle_reassign_user_command(cmd, uow, query, events),
    )
    commands.register_handler(
        unassign_user_command.UnAssignUserCommand,
        lambda cmd: unassign_user_command_handler.handle_unassign_user_command(cmd, uow, query, enrolments, events),
    )
    service = GroupMembershipService(query, commands)
    bindings._memberships = service
    return service, bindings, query, events, commands


def test_sign_in_assign_update_remove_and_events(memberships):
    service, bindings, query, events, _ = memberships
    bindings.put("p1", "team/a", ["PLATFORM_USER"])
    bindings.put("p1", "team/b", ["POWER_USER", "PLATFORM_USER"])
    events.reset_mock()
    service.sign_in("member", profile(["team/a", "team/b", "unbound"]))
    assignment = query.get_user_assignment("p1", "MEMBER")
    assert assignment.source == "GROUP"
    assert assignment.groupIds == ["team/a", "team/b"]
    assert assignment.trustedGroupIds == ["team/a", "team/b", "unbound"]
    assert assignment.roles == ["PLATFORM_USER", "POWER_USER"]
    assert assignment.userEmail == "member@example.com"
    assert isinstance(events.publish.call_args.args[0], user_assigned.UserAssigned)
    events.reset_mock()
    service.sign_in("member", profile(["team/a", "team/b", "unbound"]))
    events.publish.assert_not_called()
    service.sign_in("member", profile(["team/a"]))
    assert query.get_user_assignment("p1", "MEMBER").roles == ["PLATFORM_USER"]
    assert isinstance(events.publish.call_args.args[0], user_reassigned.UserReAssigned)
    service.sign_in("member", profile([]))
    assert query.get_user_assignment("p1", "MEMBER") is None
    assert isinstance(events.publish.call_args.args[0], user_unassigned.UserUnAssigned)


def test_binding_role_change_delete_and_empty_roles_reconcile_from_snapshot(memberships):
    service, bindings, query, events, _ = memberships
    bindings.put("p1", "a", ["PLATFORM_USER"])
    bindings.put("p1", "b", ["BETA_USER"])
    service.sign_in("member", profile(["a", "b", "c"]))
    bindings.put("p1", "a", ["POWER_USER"])
    assert set(query.get_user_assignment("p1", "MEMBER").roles) == {"POWER_USER", "BETA_USER"}
    assert isinstance(events.publish.call_args.args[0], user_reassigned.UserReAssigned)
    bindings.delete("p1", "a")
    assert query.get_user_assignment("p1", "MEMBER").groupIds == ["b"]
    bindings.put("p1", "c", ["PRODUCT_CONTRIBUTOR"])
    assert query.get_user_assignment("p1", "MEMBER").groupIds == ["b", "c"]
    bindings.put("p1", "b", [])
    assert query.get_user_assignment("p1", "MEMBER").roles == ["PRODUCT_CONTRIBUTOR"]
    bindings.delete("p1", "c")
    assert query.get_user_assignment("p1", "MEMBER") is None
    assert isinstance(events.publish.call_args.args[0], user_unassigned.UserUnAssigned)
    bindings.delete("p1", "c")


def test_sign_in_reconciles_all_projects_and_binding_changes_are_project_scoped(memberships):
    service, bindings, query, _, _ = memberships
    with bindings._uow as uow:
        uow.get_repository(project.ProjectPrimaryKey, project.Project).add(
            project.Project(projectId="p2", projectName="Two", isActive=True)
        )
        uow.commit()
    bindings.put("p1", "a", ["PLATFORM_USER"])
    bindings.put("p2", "a", ["POWER_USER"])
    service.sign_in("member", profile(["a"]))
    assert query.get_user_assignment("p1", "MEMBER").roles == ["PLATFORM_USER"]
    assert query.get_user_assignment("p2", "MEMBER").roles == ["POWER_USER"]
    bindings.delete("p1", "a")
    assert query.get_user_assignment("p1", "MEMBER") is None
    assert query.get_user_assignment("p2", "MEMBER").roles == ["POWER_USER"]
    service.sign_in("member", profile([]))
    assert query.get_user_assignment("p2", "MEMBER") is None


def test_direct_assignments_win_including_old_records(memberships):
    service, bindings, query, _, _ = memberships
    bindings.put("p1", "a", ["PLATFORM_USER"])
    before = query.get_user_assignment("p1", "USER").model_dump()
    assert before["source"] == "DIRECT"
    service.sign_in("USER", profile(["a"]))
    bindings.put("p1", "a", ["POWER_USER"])
    service.sign_in("USER", profile([]))
    bindings.delete("p1", "a")
    assert query.get_user_assignment("p1", "USER").model_dump() == before
    assert project_assignment.Assignment(userId="legacy", projectId="p1", roles=[]).source == "DIRECT"


@pytest.mark.parametrize("assign", [True, False])
def test_manual_assignment_or_reassignment_converts_to_direct(memberships, assign):
    service, bindings, query, _, commands = memberships
    bindings.put("p1", "a", ["PLATFORM_USER"])
    service.sign_in("member", profile(["a"]))
    kwargs = dict(
        project_id=project_id_value_object.from_str("p1"), roles=[user_role_value_object.from_str("BETA_USER")]
    )
    if assign:
        commands.handle(
            assign_user_command.AssignUserCommand(user_id=user_id_value_object.from_str("member"), **kwargs)
        )
    else:
        commands.handle(
            reassign_user_command.ReAssignUserCommand(
                user_ids=[user_id_value_object.from_str("member")],
                initiating_user_id=user_id_value_object.from_str("USER"),
                **kwargs
            )
        )
    assignment = query.get_user_assignment("p1", "MEMBER")
    assert (assignment.source, assignment.groupIds, assignment.trustedGroupIds) == ("DIRECT", [], [])
    assert assignment.roles == ["BETA_USER"]
    bindings.delete("p1", "a")
    service.sign_in("member", profile([]))
    assert query.get_user_assignment("p1", "MEMBER") == assignment


def test_manual_removal_stands_until_next_sign_in(memberships):
    service, bindings, query, _, commands = memberships
    bindings.put("p1", "a", ["PLATFORM_USER"])
    service.sign_in("member", profile(["a"]))
    commands.handle(
        unassign_user_command.UnAssignUserCommand(
            project_id=project_id_value_object.from_str("p1"), user_ids=[user_id_value_object.from_str("MEMBER")]
        )
    )
    bindings.put("p1", "a", ["BETA_USER"])
    assert query.get_user_assignment("p1", "MEMBER") is None
    service.sign_in("member", profile(["a"]))
    assert query.get_user_assignment("p1", "MEMBER").roles == ["BETA_USER"]


@pytest.mark.parametrize("identity", [None, "OtherProvider", "malformed"])
def test_untrusted_sign_in_cannot_create_and_removes_group_membership(memberships, identity):
    service, bindings, query, _, _ = memberships
    bindings.put("p1", "a", ["PLATFORM_USER"])
    forged = profile(["a"], identity)
    if identity is None:
        forged.pop("identities")
    if identity == "malformed":
        forged["identities"] = "not json"
    service.sign_in("member", forged)
    assert query.get_user_assignment("p1", "MEMBER") is None
    service.sign_in("member", profile(["a"]))
    service.sign_in("member", forged)
    assert query.get_user_assignment("p1", "MEMBER") is None


def test_feature_off_does_not_read_or_write_memberships(monkeypatch):
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", "")
    query, commands = Mock(), Mock()
    service = GroupMembershipService(query, commands)
    service.sign_in("member", profile(["a"]))
    service.binding_changed(Mock())
    assert query.mock_calls == commands.mock_calls == []


def test_internal_group_commands_do_not_change_a_direct_assignment(memberships):
    _, _, query, events, commands = memberships
    before = query.get_user_assignment("p1", "USER")
    commands.handle(
        reassign_user_command.ReAssignUserCommand(
            project_id=project_id_value_object.from_str("p1"),
            user_ids=[user_id_value_object.from_str("USER")],
            initiating_user_id=user_id_value_object.from_str("membership", user_id_value_object.UserIdType.Service),
            roles=[user_role_value_object.from_str("PLATFORM_USER")],
            group_ids=["a"],
            trusted_group_ids=["a"],
        )
    )
    commands.handle(
        unassign_user_command.UnAssignUserCommand(
            project_id=project_id_value_object.from_str("p1"),
            user_ids=[user_id_value_object.from_str("USER")],
            group_only=True,
        )
    )
    assert query.get_user_assignment("p1", "USER") == before
    events.publish.assert_not_called()


def test_conditional_group_removal_cannot_delete_a_promoted_direct_assignment(memberships):
    _, bindings, query, events, _ = memberships
    with pytest.raises(ConditionalCheckFailedException):
        with bindings._uow as uow:
            uow.get_repository(project_assignment.AssignmentPrimaryKey, project_assignment.Assignment).remove(
                project_assignment.AssignmentPrimaryKey(projectId="p1", userId="USER"), expected={"source": "GROUP"}
            )
            uow.commit()
    assert query.get_user_assignment("p1", "USER").roles == ["ADMIN"]
    events.publish.assert_not_called()


def test_user_assignment_query_reads_every_page_consistently(services):  # noqa: F811
    _, query, _ = services
    query._dynamodb_client = Mock()
    query._dynamodb_client.get_paginator.return_value.paginate.return_value = [
        {"Items": [dict(userId="MEMBER", projectId="p1", roles=["PLATFORM_USER"])]},
        {"Items": [dict(userId="MEMBER", projectId="p2", roles=["POWER_USER"])]},
    ]
    assert [a.projectId for a in query.list_user_assignments("MEMBER")] == ["p1", "p2"]
    assert query._dynamodb_client.get_paginator.return_value.paginate.call_args.kwargs["ConsistentRead"] is True
