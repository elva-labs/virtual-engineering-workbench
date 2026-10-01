"""SUPPORT is granted by platform admins or services (Terraform), not by program owners."""

import assertpy

from app.projects.domain.command_handlers.users import reassign_user_command_handler as command_handler
from app.projects.domain.commands.users import reassign_user_command as command
from app.projects.domain.model.project_assignment import Role
from app.projects.domain.value_objects import project_id_value_object, user_id_value_object, user_role_value_object


def test_support_is_a_valid_role():
    assertpy.assert_that(user_role_value_object.from_str("SUPPORT").value).is_equal_to(Role.SUPPORT)


def test_program_owner_cannot_grant_support(
    handler_dependencies,
    sample_project,
    user_sample_assignment,
    owner_sample_assignment,
    mock_uow_2,
    mock_assignments_repo,
):
    cmd = command.ReAssignUserCommand(
        project_id=project_id_value_object.from_str(user_sample_assignment().projectId),
        user_ids=[user_id_value_object.from_str(user_sample_assignment().userId)],
        initiating_user_id=user_id_value_object.from_str(owner_sample_assignment.userId),
        roles=[user_role_value_object.from_str(Role.SUPPORT), user_role_value_object.from_str(Role.PLATFORM_USER)],
    )
    _, projects_query_service_mock, message_bus_mock = handler_dependencies
    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.get_user_assignment.side_effect = [owner_sample_assignment, user_sample_assignment()]

    command_handler.handle_reassign_user_command(
        cmd=cmd,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
    )

    _, ent = mock_assignments_repo.update_entity.call_args.kwargs.values()
    assertpy.assert_that(ent.roles).does_not_contain(Role.SUPPORT)


def test_service_can_grant_support(
    handler_dependencies, sample_project, user_sample_assignment, mock_uow_2, mock_assignments_repo
):
    cmd = command.ReAssignUserCommand(
        project_id=project_id_value_object.from_str("123"),
        user_ids=[user_id_value_object.from_str("U0")],
        initiating_user_id=user_id_value_object.from_str("svc-client-id", user_id_value_object.UserIdType.Service),
        roles=[user_role_value_object.from_str(Role.SUPPORT)],
    )
    _, projects_query_service_mock, message_bus_mock = handler_dependencies
    projects_query_service_mock.get_project_by_id.return_value = sample_project
    projects_query_service_mock.get_user_assignment.side_effect = [user_sample_assignment()]

    command_handler.handle_reassign_user_command(
        cmd=cmd,
        unit_of_work=mock_uow_2,
        projects_query_service=projects_query_service_mock,
        message_bus=message_bus_mock,
    )

    _, ent = mock_assignments_repo.update_entity.call_args.kwargs.values()
    assertpy.assert_that(ent.roles).contains_only(Role.SUPPORT)
