from unittest import mock

import pytest

from app.authorization.domain.integration_event_handlers.projects import project_updated_handler
from app.authorization.domain.integration_events.projects.project_updated import ProjectUpdated
from app.authorization.domain.read_models import project_settings
from app.shared.adapters.unit_of_work_v2 import unit_of_work


@pytest.fixture
def mocked_repo():
    return mock.create_autospec(spec=unit_of_work.GenericRepository)


@pytest.fixture
def mocked_uow(mocked_repo):
    m = mock.create_autospec(spec=unit_of_work.UnitOfWork)
    m.get_repository.return_value = mocked_repo
    return m


def test_should_store_settings_of_a_project_without_settings(mocked_uow, mocked_repo):
    mocked_repo.get.return_value = None

    project_updated_handler.handle(ProjectUpdated(projectId="proj-1", remoteSupportEnabled=False), mocked_uow)

    mocked_repo.add.assert_called_once_with(
        project_settings.ProjectSettings(projectId="proj-1", remoteSupportEnabled=False)
    )
    mocked_uow.commit.assert_called_once()


def test_should_update_existing_settings(mocked_uow, mocked_repo):
    existing = project_settings.ProjectSettings(projectId="proj-1", remoteSupportEnabled=False)
    mocked_repo.get.return_value = existing

    project_updated_handler.handle(ProjectUpdated(projectId="proj-1", remoteSupportEnabled=True), mocked_uow)

    assert existing.remoteSupportEnabled is True
    mocked_repo.update_entity.assert_called_once()
    mocked_uow.commit.assert_called_once()


def test_should_ignore_events_without_the_setting(mocked_uow, mocked_repo):
    project_updated_handler.handle(ProjectUpdated(projectId="proj-1"), mocked_uow)

    mocked_uow.get_repository.assert_not_called()
    mocked_uow.commit.assert_not_called()
