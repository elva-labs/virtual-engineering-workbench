"""Workbench-only programs: the authorizer keeps the roles such a program has use for and
closes product management to everyone but an ADMIN."""

from unittest import mock

import pytest

from app.authorization.domain.integration_event_handlers.projects import project_updated_handler
from app.authorization.domain.integration_events.projects import project_updated
from app.authorization.domain.ports import assignments_query_service
from app.authorization.domain.read_models import project_assignment, project_settings
from app.authorization.domain.services.auth import authorizer, authorizer_steps
from app.shared.adapters.unit_of_work_v2 import unit_of_work

PATH = "/projects/{projectId}/products/available"


def _request() -> authorizer.AuthorizationRequest:
    return authorizer.AuthorizationRequest(
        auth_token="t",
        api_id="api",
        operation_id="GetAvailableProducts",
        resource_ids={"projectId": "proj-1"},
        resource_path=PATH,
        resource="r",
    )


def _context(bounded_context="provisioning", groups=()) -> authorizer.AuthorizationContext:
    return authorizer.AuthorizationContext(
        user_name="USER-1",
        trusted_group_ids=list(groups),
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context=bounded_context),
        project_scoped_bounded_contexts=["provisioning", "packaging", "publishing", "projects"],
    )


def _query_service(roles, experience="workbench-only"):
    qs = mock.create_autospec(spec=assignments_query_service.AssignmentsQueryService, instance=True)
    qs.get_user_assignments.return_value = [
        project_assignment.Assignment(userId="USER-1", projectId="proj-1", roles=roles)
    ]
    qs.get_group_assignments.return_value = []
    qs.get_project_settings.return_value = project_settings.ProjectSettings(projectId="proj-1", experience=experience)
    return qs


def _enrich(qs, context, platform_admin_groups=()):
    return authorizer_steps.ProjectsBCContextEnricher(
        assignments_query_service=qs, platform_admin_groups=list(platform_admin_groups)
    ).invoke(_request(), context)


@pytest.mark.parametrize(
    "roles, expected",
    [
        (["PROGRAM_OWNER", "PRODUCT_CONTRIBUTOR"], ["PLATFORM_USER", "PROGRAM_OWNER"]),
        (["POWER_USER"], ["PLATFORM_USER"]),
        (["BETA_USER", "SUPPORT"], ["PLATFORM_USER", "SUPPORT"]),
        (["PLATFORM_USER"], ["PLATFORM_USER"]),
    ],
)
def test_members_keep_the_roles_a_workbench_only_program_has_use_for(roles, expected):
    context = _context()

    assert _enrich(_query_service(roles), context)

    assert sorted(context.roles) == sorted(expected)
    # The Cedar principal is built from the assignments, so they carry the reduced roles as well.
    assignment = next(a for a in context.project_assignments if a.projectId == "proj-1")
    assert sorted(assignment.roles) == sorted(expected)
    assert context.project_experience == "workbench-only"


def test_an_admin_keeps_everything():
    context = _context()

    assert _enrich(_query_service(["ADMIN", "PRODUCT_CONTRIBUTOR"]), context)

    assert sorted(context.roles) == ["ADMIN", "PRODUCT_CONTRIBUTOR"]


def test_a_platform_admin_keeps_everything_and_reaches_product_management():
    context = _context(bounded_context="packaging", groups=["admins"])

    assert _enrich(_query_service(["PLATFORM_USER"]), context, platform_admin_groups=["admins"])

    assert "ADMIN" in context.roles


@pytest.mark.parametrize("bounded_context", ["packaging", "publishing"])
def test_product_management_is_closed_to_members(bounded_context):
    context = _context(bounded_context=bounded_context)

    assert _enrich(_query_service(["PROGRAM_OWNER"]), context) is False


def test_a_full_program_is_unchanged():
    context = _context(bounded_context="packaging")

    assert _enrich(_query_service(["PROGRAM_OWNER", "PRODUCT_CONTRIBUTOR"], experience=None), context)

    assert sorted(context.roles) == ["PRODUCT_CONTRIBUTOR", "PROGRAM_OWNER"]
    assert context.project_experience is None


def _settings_uow(stored):
    uow = mock.create_autospec(unit_of_work.UnitOfWork, instance=True)
    repo = mock.create_autospec(unit_of_work.GenericRepository, instance=True)
    repo.get.return_value = stored
    uow.get_repository.return_value = repo
    uow.__enter__.return_value = uow
    return uow, repo


def test_project_updated_stores_the_experience():
    stored = project_settings.ProjectSettings(projectId="proj-1")
    uow, repo = _settings_uow(stored)

    project_updated_handler.handle(project_updated.ProjectUpdated(projectId="proj-1", experience="workbench-only"), uow)

    assert stored.experience == "workbench-only"
    repo.update_entity.assert_called_once()


def test_an_older_event_keeps_the_stored_experience():
    stored = project_settings.ProjectSettings(projectId="proj-1", experience="workbench-only")
    uow, _ = _settings_uow(stored)

    project_updated_handler.handle(project_updated.ProjectUpdated(projectId="proj-1", remoteSupportEnabled=True), uow)

    assert stored.experience == "workbench-only"
