"""a pending enrolment past the first page of 50 is found, so no duplicate is created."""

from unittest import mock

from app.projects.domain.command_handlers.enrolments import enrol_user_to_program_command_handler as command_handler
from app.projects.domain.commands.enrolments import enrol_user_to_program_command as command
from app.projects.domain.model import enrolment
from app.projects.domain.ports import enrolment_query_service
from app.projects.domain.value_objects import (
    project_id_value_object,
    source_system_value_object,
    user_email_value_object,
    user_id_value_object,
)


def _pending(project_id: str) -> enrolment.Enrolment:
    return enrolment.Enrolment(projectId=project_id, userId="t001", status=enrolment.EnrolmentStatus.Pending)


def test_pending_enrolment_on_a_later_page_is_found(mock_uow_2, mock_enrolments_repo, mock_projects_qs, message_bus_mock):
    pages = [[_pending(f"p-{i}") for i in range(start, start + 50)] for start in range(0, 300, 50)]
    tokens = [{"offset": 50 * (n + 1)} for n in range(5)] + [None]
    enrolment_service = mock.create_autospec(spec=enrolment_query_service.EnrolmentQueryService, instance=True)
    enrolment_service.list_enrolments_by_user.side_effect = list(zip(pages, tokens))

    result = command_handler.handle_enrol_user_to_program_command(
        cmd=command.EnrolUserToProgramCommand(
            project_id=project_id_value_object.from_str("p-260"),
            user_id=user_id_value_object.from_str("t001"),
            user_email=user_email_value_object.from_str("marty@mcfly.com"),
            source_system=source_system_value_object.from_str("VEW"),
        ),
        uow=mock_uow_2,
        projects_qry_srv=mock_projects_qs,
        enrolment_qry_srv=enrolment_service,
        msg_bus=message_bus_mock,
    )

    assert result == pages[5][10].id
    assert enrolment_service.list_enrolments_by_user.call_count == 6
    assert enrolment_service.list_enrolments_by_user.call_args_list[5].kwargs["next_token"] == {"offset": 250}
    mock_enrolments_repo.add.assert_not_called()
    message_bus_mock.publish.assert_not_called()
