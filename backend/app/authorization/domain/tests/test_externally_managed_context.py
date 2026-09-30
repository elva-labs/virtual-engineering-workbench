"""The authorizer passes whether an external tool manages the project in the path to the user APIs."""

from unittest import mock

import pytest

from app.authorization.domain.integration_event_handlers.projects import project_updated_handler
from app.authorization.domain.integration_events.projects.project_updated import ProjectUpdated
from app.authorization.domain.ports import assignments_query_service
from app.authorization.domain.read_models import project_settings
from app.authorization.domain.services.auth import authorizer, authorizer_steps
from app.shared.adapters.unit_of_work_v2 import unit_of_work

SOURCE = "example-org/config programs/example"


def _request(resource_ids: dict, resource_path: str) -> authorizer.AuthorizationRequest:
    return authorizer.AuthorizationRequest(
        auth_token="t",
        api_id="api",
        operation_id="CreateRecipe",
        resource_ids=resource_ids,
        resource_path=resource_path,
        resource="r",
    )


def _context() -> authorizer.AuthorizationContext:
    return authorizer.AuthorizationContext(
        user_name="USER-1",
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context="packaging"),
        project_scoped_bounded_contexts=["packaging"],
    )


def _query_service(settings: project_settings.ProjectSettings):
    qs = mock.create_autospec(spec=assignments_query_service.AssignmentsQueryService, instance=True)
    qs.get_user_assignments.return_value = []
    qs.get_group_assignments.return_value = []
    qs.get_project_settings.return_value = settings
    return qs


@pytest.fixture
def mocked_repo():
    return mock.create_autospec(spec=unit_of_work.GenericRepository)


@pytest.fixture
def mocked_uow(mocked_repo):
    m = mock.create_autospec(spec=unit_of_work.UnitOfWork)
    m.get_repository.return_value = mocked_repo
    return m


def test_the_mark_is_stored(mocked_uow, mocked_repo):
    mocked_repo.get.return_value = None

    project_updated_handler.handle(
        ProjectUpdated.model_validate({"projectId": "proj-1", "managedBy": "terraform", "managedSource": SOURCE}),
        mocked_uow,
    )

    stored = mocked_repo.add.call_args.args[0]
    assert (stored.managedBy, stored.managedSource) == ("terraform", SOURCE)


def test_a_null_mark_clears_it(mocked_uow, mocked_repo):
    existing = project_settings.ProjectSettings(projectId="proj-1", managedBy="terraform", managedSource=SOURCE)
    mocked_repo.get.return_value = existing

    project_updated_handler.handle(
        ProjectUpdated.model_validate({"projectId": "proj-1", "managedBy": None, "managedSource": None}), mocked_uow
    )

    assert (existing.managedBy, existing.managedSource) == (None, None)
    mocked_repo.update_entity.assert_called_once()


def test_events_without_the_mark_keep_it(mocked_uow, mocked_repo):
    # Events from before the management mode existed.
    project_updated_handler.handle(ProjectUpdated.model_validate({"projectId": "proj-1"}), mocked_uow)

    mocked_uow.get_repository.assert_not_called()


def test_the_enricher_reads_the_mark_of_the_project_in_the_path():
    qs = _query_service(
        project_settings.ProjectSettings(projectId="proj-1", managedBy="terraform", managedSource=SOURCE)
    )
    context = _context()

    authorizer_steps.ProjectsBCContextEnricher(assignments_query_service=qs).invoke(
        _request({"projectId": "proj-1"}, "/projects/{projectId}/recipes"), context
    )

    qs.get_project_settings.assert_called_once_with(project_id="proj-1")
    assert (context.project_managed_by, context.project_managed_source) == ("terraform", SOURCE)


class _Step(authorizer.AuthorizerStep):
    def __init__(self, managed_by):
        self.managed_by = managed_by

    def invoke(self, request, context):
        context.user_name = "user-1"
        context.project_managed_by = self.managed_by
        context.project_managed_source = SOURCE if self.managed_by else None
        return True


@pytest.mark.parametrize("managed_by,expected", [("terraform", "terraform"), (None, "")])
def test_the_policy_context_carries_the_mark_as_a_string(managed_by, expected):
    auth = authorizer.Authorizer(
        logger=mock.MagicMock(),
        metrics=mock.MagicMock(),
        api_config_provider=lambda api_id: authorizer.APIAuthConfig(api_id=api_id),
        authorization_steps=[_Step(managed_by)],
    )

    policy = auth.authorize(_request({"projectId": "proj-1"}, "/projects/{projectId}/recipes"))

    assert policy["context"]["projectManagedBy"] == expected
    assert policy["context"]["projectManagedSource"] == (SOURCE if managed_by else "")
