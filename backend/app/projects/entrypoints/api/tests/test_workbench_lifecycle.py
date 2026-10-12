"""Portal project policy: persistence, inherited defaults, bounded inputs and scoped edit rights."""

import json
from unittest import mock

import pytest

from app.projects.domain.model.project import Project
from app.projects.domain.project_lifecycle_service import ProjectLifecycleService
from app.projects.entrypoints.api import bootstrapper
from app.projects.entrypoints.api.tests import fake_classes
from app.shared.adapters.message_bus import in_memory_command_bus

PROJECT = "proj-test1"
PATH = f"/projects/{PROJECT}/workbench-lifecycle"
PLATFORM = {
    "idleStopMinutes": 90,
    "nightlyStop": False,
    "nightlyStopTime": "22:15",
    "timezone": "Europe/Stockholm",
    "weekendStop": True,
}
DEPLOYMENT = {"idleStopEnabled": False, "nightlyStopScheduleEnabled": True}


@pytest.fixture
def lifecycle_api(monkeypatch):
    monkeypatch.setenv("WORKBENCH_LIFECYCLE_DEFAULTS", json.dumps({**PLATFORM, "idleStop": {"enabled": False}}))
    monkeypatch.setenv("WORKBENCH_LIFECYCLE_DEPLOYMENT", json.dumps(DEPLOYMENT))
    project = Project(projectId=PROJECT, projectName="Company", isActive=True)
    query = fake_classes.FakeProjectsQueryService()
    query.get_project_by_id = lambda id: project if id == PROJECT else None
    uow = mock.MagicMock()
    uow.get_repository.return_value.get.side_effect = lambda pk: project if pk.projectId == PROJECT else None
    deps = bootstrapper.Dependencies(
        projects_query_service=query,
        technologies_query_service=fake_classes.FakeTechnologiesQueryService(),
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(),
        command_bus=in_memory_command_bus.InMemoryCommandBus(logger=mock.Mock()),
        project_lifecycle_service=ProjectLifecycleService(uow, query, mock.Mock()),
    )
    from app.projects.entrypoints.api import handler

    monkeypatch.setattr(handler, "dependencies", deps)
    return handler, project, uow


def _event(authenticated_event, method="GET", body=None, roles=("ADMIN",), path=PATH):
    event = authenticated_event(json.dumps(body) if body is not None else None, path, method)
    event["requestContext"]["authorizer"]["userRoles"] = json.dumps(roles)
    return event


def test_get_inherited_policy_is_200_and_reports_deployment(lifecycle_api, authenticated_event, lambda_context):
    handler, _, uow = lifecycle_api
    result = handler.handler(_event(authenticated_event), lambda_context)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {
        "projectId": PROJECT,
        "settings": None,
        "platformDefaults": PLATFORM,
        "deployment": DEPLOYMENT,
        "canEdit": True,
        "managedBy": None,
        "managedSource": None,
    }
    uow.commit.assert_not_called()


@pytest.mark.parametrize("role", ["ADMIN", "PROGRAM_OWNER"])
def test_managers_can_replace_and_reset(lifecycle_api, authenticated_event, lambda_context, role):
    handler, project, uow = lifecycle_api
    settings = {
        "alwaysOn": True,
        "idleStopMinutes": 10,
        "nightlyStop": True,
        "weekendStop": False,
        "allowUserDisableNightlyStop": True,
        "allowUserIdleTimeout": True,
        "userIdleTimeoutMinMinutes": 10,
        "userIdleTimeoutMaxMinutes": 1440,
    }
    result = handler.handler(_event(authenticated_event, "PUT", settings, roles=[role]), lambda_context)
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["settings"] == settings
    assert project.workbenchLifecycle.model_dump() == settings
    uow.commit.assert_called_once()
    assert project.lastUpdateDate is not None
    get = handler.handler(_event(authenticated_event, roles=[role]), lambda_context)
    assert json.loads(get["body"])["settings"] == settings
    delete = handler.handler(_event(authenticated_event, "DELETE", roles=[role]), lambda_context)
    assert delete["statusCode"] == 200
    assert json.loads(delete["body"])["settings"] is None
    assert json.loads(delete["body"])["platformDefaults"] == PLATFORM
    assert project.workbenchLifecycle is None
    assert uow.commit.call_count == 2
    handler.handler(_event(authenticated_event, "DELETE", roles=[role]), lambda_context)
    assert uow.commit.call_count == 2


def test_nullable_overrides_remain_null(lifecycle_api, authenticated_event, lambda_context):
    handler, _, _ = lifecycle_api
    result = handler.handler(
        _event(authenticated_event, "PUT", {"idleStopMinutes": None, "nightlyStop": None}), lambda_context
    )
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["settings"] == {
        "alwaysOn": False,
        "idleStopMinutes": None,
        "nightlyStop": None,
        "weekendStop": None,
        "allowUserDisableNightlyStop": False,
        "allowUserIdleTimeout": False,
        "userIdleTimeoutMinMinutes": 30,
        "userIdleTimeoutMaxMinutes": 480,
    }


@pytest.mark.parametrize("role", ["PLATFORM_USER", "POWER_USER", "PRODUCT_CONTRIBUTOR", "BETA_USER", "SUPPORT"])
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_non_managers_cannot_write(lifecycle_api, authenticated_event, lambda_context, role, method):
    handler, project, uow = lifecycle_api
    event = _event(authenticated_event, method, {} if method == "PUT" else None, roles=[role])
    event["headers"]["X-User-Roles"] = "ADMIN"
    result = handler.handler(event, lambda_context)
    assert result["statusCode"] == 403
    assert "admins and program owners" in json.loads(result["body"])["message"]
    assert project.workbenchLifecycle is None
    uow.commit.assert_not_called()
    read = handler.handler(_event(authenticated_event, roles=[role]), lambda_context)
    assert read["statusCode"] == 200
    assert json.loads(read["body"])["canEdit"] is False


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_unknown_project_is_404(lifecycle_api, authenticated_event, lambda_context, method):
    handler, _, uow = lifecycle_api
    event = _event(
        authenticated_event, method, {} if method == "PUT" else None, path="/projects/proj-missing/workbench-lifecycle"
    )
    result = handler.handler(event, lambda_context)
    assert result["statusCode"] == 404
    assert json.loads(result["body"])["message"] == "Project not found"
    uow.commit.assert_not_called()


@pytest.mark.parametrize("context_managed,persisted_managed", [(True, True), (True, False), (False, True)])
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_external_ownership_refuses_writes_and_disables_editing(
    lifecycle_api, authenticated_event, lambda_context, context_managed, persisted_managed, method
):
    handler, project, uow = lifecycle_api
    if persisted_managed:
        project.managedBy = "terraform"
        project.managedSource = "company.tf"
    event = _event(authenticated_event, method, {} if method == "PUT" else None)
    if context_managed:
        event["requestContext"]["authorizer"].update(projectManagedBy="terraform", projectManagedSource="company.tf")
    result = handler.handler(event, lambda_context)
    assert result["statusCode"] == 409
    assert json.loads(result["body"])["code"] == "PROJECT_EXTERNALLY_MANAGED"
    uow.commit.assert_not_called()
    event["httpMethod"] = "GET"
    event["body"] = None
    read = handler.handler(event, lambda_context)
    assert read["statusCode"] == 200
    assert json.loads(read["body"])["canEdit"] is False
    if persisted_managed:
        assert json.loads(read["body"])["managedBy"] == "terraform"
        assert json.loads(read["body"])["managedSource"] == "company.tf"


@pytest.mark.parametrize(
    "settings",
    [
        {"idleStopMinutes": 9},
        {"idleStopMinutes": 1441},
        {"userIdleTimeoutMinMinutes": 9},
        {"userIdleTimeoutMaxMinutes": 1441},
        {"userIdleTimeoutMinMinutes": 200, "userIdleTimeoutMaxMinutes": 100},
        {"idleTimeoutMinutes": 100},
        {"alwaysOn": None},
    ],
)
def test_invalid_policy_rejected_without_write(lifecycle_api, authenticated_event, lambda_context, settings):
    handler, project, uow = lifecycle_api
    result = handler.handler(_event(authenticated_event, "PUT", settings), lambda_context)
    assert result["statusCode"] == 422
    assert "detail" in json.loads(result["body"])
    assert project.workbenchLifecycle is None
    uow.commit.assert_not_called()
