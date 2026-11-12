import json
import logging
from unittest.mock import Mock, create_autospec

import pytest

from app.projects.adapters.services.cognito_user_directory_service import CognitoUserDirectoryService
from app.projects.domain.model import project, project_assignment, project_group_assignment
from app.projects.domain.ports import projects_query_service
from app.projects.domain.project_group_assignment_service import ProjectGroupAssignmentService
from app.projects.entrypoints.api import bootstrapper
from app.projects.entrypoints.api.tests import fake_classes
from app.shared.adapters.message_bus.in_memory_command_bus import InMemoryCommandBus
from app.shared.identity import oidc_groups

GROUP = "engineering/team"
PROVIDER = "ExampleOIDC"


@pytest.mark.parametrize("enabled", [False, True])
def test_members_include_source_only_when_enabled(
    setup_handler, monkeypatch, authenticated_event, lambda_context, enabled
):
    handler, query, _, _, _ = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER if enabled else "")
    query.list_users_by_project.return_value = [
        project_assignment.Assignment(
            userId="USER123",
            projectId="proj-bound",
            roles=["PLATFORM_USER"],
            source="GROUP",
            groupIds=[GROUP],
            trustedGroupIds=[GROUP, "other"],
        )
    ]
    response = handler.handler(authenticated_event(None, "/projects/proj-bound/users", "GET"), lambda_context)
    assert response["statusCode"] == 200
    member = json.loads(response["body"])["assignments"][0]
    assert "trustedGroupIds" not in member
    if enabled:
        assert member["source"] == "GROUP" and member["groupIds"] == [GROUP]
    else:
        assert "source" not in member and "groupIds" not in member


def _grant(project_id="proj-bound", roles=None):
    return project_group_assignment.ProjectGroupAssignment(
        projectId=project_id,
        groupId=GROUP,
        roles=roles or ["PLATFORM_USER"],
        version=1,
        createDate="now",
        lastUpdateDate="now",
        groupName="Engineering",
    )


@pytest.fixture
def setup_handler(monkeypatch):
    from app.projects.entrypoints.api import handler

    query = create_autospec(projects_query_service.ProjectsQueryService, instance=True)
    query.list_projects.return_value = (
        [project.Project(projectId="proj-bound", projectName="Bound", isActive=True)],
        None,
        [],
    )
    query.get_user_assignment.return_value = None
    query.get_group_assignments.return_value = [_grant()]
    query.list_project_group_assignments.return_value = [_grant()]
    user_info = Mock()
    cognito = Mock()
    directory = CognitoUserDirectoryService(cognito, "pool", logging.getLogger(__name__))
    service = create_autospec(ProjectGroupAssignmentService, instance=True)
    service.put.return_value = _grant()
    dependencies = bootstrapper.Dependencies(
        projects_query_service=query,
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(),
        technologies_query_service=fake_classes.FakeTechnologiesQueryService(),
        command_bus=InMemoryCommandBus(logger=logging.getLogger(__name__)),
        user_info_client=user_info,
        user_directory_service=directory,
        group_assignment_service=service,
    )
    monkeypatch.setattr(handler, "dependencies", dependencies)
    return handler, query, user_info, cognito, service


def _profile(identities):
    profile = {
        "custom:user_tid": "user123",
        oidc_groups.GROUPS_ATTRIBUTE: json.dumps([GROUP]),
        "email": "user@example.test",
    }
    if identities is not None:
        profile["identities"] = identities
    return profile


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("federated", [False, True])
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("path", ["/projects", "/internal/projects/proj-bound/users/user123"])
def test_all_project_role_paths_require_linked_identity_and_opt_in(
    setup_handler, monkeypatch, authenticated_event, lambda_context, enabled, federated, direct, path
):
    handler, query, user_info, cognito, _ = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER if enabled else "")
    profile = _profile(json.dumps([{"providerName": PROVIDER}]) if federated else None)
    if direct:
        assignment = project_assignment.Assignment(userId="USER123", projectId="proj-bound", roles=["BETA_USER"])
        query.get_user_assignment.return_value = assignment
        projects, token, _ = query.list_projects.return_value
        query.list_projects.return_value = (projects, token, [assignment])
    user_info.return_value = profile
    cognito.get_paginator.return_value.paginate.return_value = [
        {"Users": [{"Attributes": [{"Name": key, "Value": value} for key, value in profile.items()]}]}
    ]
    response = handler.handler(authenticated_event(None, path, "GET", {"pageSize": "10"}), lambda_context)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assignments = body["assignments"] if path == "/projects" else ([body["assignment"]] if body["assignment"] else [])
    expected = (["BETA_USER"] if direct else []) + (["PLATFORM_USER"] if enabled and federated else [])
    assert [a["roles"] for a in assignments] == ([expected] if expected else [])
    if not enabled:
        user_info.assert_not_called()
        cognito.get_paginator.assert_not_called()
    if not enabled or not federated:
        query.get_group_assignments.assert_not_called()


@pytest.mark.parametrize("identities", ["invalid", '[{"providerName":"Other"}]', "{}", "[]"])
def test_stored_groups_fail_closed_for_untrusted_identities(setup_handler, monkeypatch, identities):
    _, _, _, cognito, _ = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    cognito.get_paginator.return_value.paginate.return_value = [
        {"Users": [{"Attributes": [{"Name": key, "Value": value} for key, value in _profile(identities).items()]}]}
    ]
    directory = CognitoUserDirectoryService(cognito, "pool", logging.getLogger(__name__))
    assert directory.get_user_group_ids("USER123") == []


def test_listing_keeps_paging_and_unions_direct_roles(setup_handler, monkeypatch, authenticated_event, lambda_context):
    handler, query, user_info, _, _ = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    user_info.return_value = _profile(json.dumps([{"providerName": PROVIDER}]))
    direct = project_assignment.Assignment(userId="USER123", projectId="proj-bound", roles=["ADMIN"])
    query.get_user_assignment.return_value = direct
    projects, _, _ = query.list_projects.return_value
    query.list_projects.return_value = (projects, {"PK": "next"}, [direct])
    result = handler.handler(authenticated_event(None, "/projects", "GET", {"pageSize": "1"}), lambda_context)
    body = json.loads(result["body"])
    assert body["assignments"][0]["roles"] == ["ADMIN", "PLATFORM_USER"]
    query.list_projects.assert_called_once_with(page_size=1, next_token=None, user_id="USER123")
    assert direct.roles == ["ADMIN"]


@pytest.mark.parametrize("profile", [{}, {"custom:user_tid": "OTHER"}])
def test_user_info_must_match_authenticated_user(
    setup_handler, monkeypatch, authenticated_event, lambda_context, profile
):
    handler, query, user_info, _, _ = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    user_info.return_value = {**_profile(json.dumps([{"providerName": PROVIDER}])), **profile}
    if not profile:
        user_info.return_value.pop("custom:user_tid")
    result = handler.handler(authenticated_event(None, "/projects", "GET", {"pageSize": "10"}), lambda_context)
    assert json.loads(result["body"])["assignments"] == []
    query.get_group_assignments.assert_not_called()


def test_user_info_failure_preserves_direct_access(setup_handler, monkeypatch, authenticated_event, lambda_context):
    handler, query, user_info, _, _ = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    user_info.side_effect = RuntimeError("unavailable")
    direct = project_assignment.Assignment(userId="USER123", projectId="proj-bound", roles=["ADMIN"])
    projects, _, _ = query.list_projects.return_value
    query.list_projects.return_value = (projects, None, [direct])
    response = handler.handler(authenticated_event(None, "/projects", "GET", {"pageSize": "10"}), lambda_context)
    assert json.loads(response["body"])["assignments"][0]["roles"] == ["ADMIN"]


@pytest.mark.parametrize(
    "method,body",
    [("GET", None), ("PUT", {"groupId": GROUP, "roles": ["PLATFORM_USER"]}), ("DELETE", {"groupId": GROUP})],
)
def test_management_routes(setup_handler, monkeypatch, authenticated_event, lambda_context, method, body):
    handler, _, _, _, service = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    response = handler.handler(
        authenticated_event(json.dumps(body) if body else None, "/projects/proj-bound/groups", method), lambda_context
    )
    assert response["statusCode"] == 200
    if method == "PUT":
        service.put.assert_called_once_with("proj-bound", GROUP, ["PLATFORM_USER"], None)
    elif method == "DELETE":
        service.delete.assert_called_once_with("proj-bound", GROUP)
    else:
        assert json.loads(response["body"])["assignments"][0]["groupId"] == GROUP


@pytest.mark.parametrize("roles", [["SUPPORT"], ["INVALID"]])
def test_binding_rejects_roles_not_in_project_model(
    setup_handler, monkeypatch, authenticated_event, lambda_context, roles
):
    handler, _, _, _, service = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    response = handler.handler(
        authenticated_event(json.dumps({"groupId": GROUP, "roles": roles}), "/projects/proj-bound/groups", "PUT"),
        lambda_context,
    )
    assert response["statusCode"] == 422
    service.put.assert_not_called()


def test_disabled_binding_routes_are_unavailable(setup_handler, monkeypatch, authenticated_event, lambda_context):
    handler, query, _, _, _ = setup_handler
    monkeypatch.delenv("OIDC_GROUPS_PROVIDER_NAME", raising=False)
    response = handler.handler(authenticated_event(None, "/projects/proj-bound/groups", "GET"), lambda_context)
    assert response["statusCode"] == 404
    query.list_project_group_assignments.assert_not_called()


@pytest.mark.parametrize("federated", [False, True])
def test_regular_user_roles_resolve_trusted_groups(
    setup_handler, monkeypatch, authenticated_event, lambda_context, federated
):
    handler, _, _, cognito, _ = setup_handler
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    profile = _profile(json.dumps([{"providerName": PROVIDER}]) if federated else None)
    cognito.get_paginator.return_value.paginate.return_value = [
        {"Users": [{"Attributes": [{"Name": key, "Value": value} for key, value in profile.items()]}]}
    ]
    response = handler.handler(authenticated_event(None, "/projects/proj-bound/users/user123", "GET"), lambda_context)
    assert json.loads(response["body"])["roles"] == (["PLATFORM_USER"] if federated else [])
