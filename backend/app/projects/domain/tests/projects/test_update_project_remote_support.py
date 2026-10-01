"""The per-project remote-support switch."""

import unittest

import pytest

from app.projects.domain.command_handlers.projects import update_project_command_handler
from app.projects.domain.commands.projects import update_project_command
from app.projects.domain.events.projects import project_updated
from app.projects.domain.model import project
from app.projects.domain.value_objects import project_id_value_object
from app.shared.adapters.message_bus import message_bus


@pytest.fixture
def message_bus_mock():
    return unittest.mock.create_autospec(spec=message_bus.MessageBus, instance=True)


@pytest.fixture
def existing_project():
    return project.Project(projectId="proj-12345", projectName="p", projectDescription="d", isActive=True)


def _command(remote_support_enabled):
    return update_project_command.UpdateProjectCommand(
        id=project_id_value_object.from_str("proj-12345"),
        name="p",
        description="d",
        isActive=True,
        remoteSupportEnabled=remote_support_enabled,
    )


def test_new_projects_allow_remote_support_by_default():
    assert project.Project(projectName="p", isActive=True).remoteSupportEnabled is True


def test_switching_remote_support_off_is_stored_and_published(
    existing_project, mock_projects_repo, mock_uow_2, message_bus_mock
):
    mock_projects_repo.get.return_value = existing_project

    update_project_command_handler.handle_update_project_command(
        cmd=_command(False), uow=mock_uow_2, msg_bus=message_bus_mock
    )

    assert mock_projects_repo.update_entity.call_args.kwargs["entity"].remoteSupportEnabled is False
    event = message_bus_mock.publish.call_args.args[0]
    assert isinstance(event, project_updated.ProjectUpdated)
    assert event.remote_support_enabled is False
    assert event.model_dump(by_alias=True)["remoteSupportEnabled"] is False


def test_omitting_the_setting_keeps_the_current_value(
    existing_project, mock_projects_repo, mock_uow_2, message_bus_mock
):
    existing_project.remoteSupportEnabled = False
    mock_projects_repo.get.return_value = existing_project

    update_project_command_handler.handle_update_project_command(
        cmd=_command(None), uow=mock_uow_2, msg_bus=message_bus_mock
    )

    assert mock_projects_repo.update_entity.call_args.kwargs["entity"].remoteSupportEnabled is False
    assert message_bus_mock.publish.call_args.args[0].remote_support_enabled is False
