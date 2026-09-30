"""Platform-admin groups on top of the Entra group grants (test_entra_group_access.py): members are
ADMIN on every project, and the authorizer passes the sign-in's groups on to the backend Lambdas."""

from unittest import mock

from app.authorization.domain.ports import assignments_query_service
from app.authorization.domain.read_models import project_assignment, project_group_assignment, project_settings
from app.authorization.domain.services.auth import authorizer, authorizer_steps

G_USERS = "11111111-1111-1111-1111-111111111111"
G_PLATFORM = "6ef5c9f6-de67-4103-8695-6e70d688794a"


def _request(resource_ids: dict, resource_path: str = "/projects/{projectId}") -> authorizer.AuthorizationRequest:
    return authorizer.AuthorizationRequest(
        auth_token="Bearer t",
        api_id="api",
        operation_id="GetProject",
        resource_ids=resource_ids,
        resource_path=resource_path,
        resource="r",
    )


def _context(groups: list[str]) -> authorizer.AuthorizationContext:
    return authorizer.AuthorizationContext(
        user_name="USER-1",
        user_email="user@example.com",
        trusted_group_ids=groups,
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context="projects"),
        project_scoped_bounded_contexts=["projects"],
    )


def _grant(group_id: str, role: str, project_id: str = "proj-1") -> project_group_assignment.GroupAssignment:
    return project_group_assignment.GroupAssignment(groupId=group_id, projectId=project_id, roles=[role], version=1)


def _query_service(assignments=(), grants=()):
    qs = mock.create_autospec(spec=assignments_query_service.AssignmentsQueryService, instance=True)
    qs.get_user_assignments.return_value = list(assignments)
    qs.get_group_assignments.return_value = list(grants)
    qs.get_project_settings.return_value = project_settings.ProjectSettings(projectId="proj-1")
    return qs


def _enrich(qs, context, resource_ids={"projectId": "proj-1"}, resource_path="/projects/{projectId}"):
    step = authorizer_steps.ProjectsBCContextEnricher(assignments_query_service=qs, platform_admin_groups=[G_PLATFORM])
    assert step.invoke(_request(resource_ids, resource_path), context)
    return context


def _direct(project_id="proj-1", roles=("PLATFORM_USER",)):
    return project_assignment.Assignment(userId="USER-1", projectId=project_id, roles=list(roles))


def test_platform_admin_group_is_admin_on_every_project():
    context = _enrich(_query_service(), _context([G_PLATFORM]))

    assert context.roles == ["ADMIN"]


def test_platform_admin_keeps_the_direct_and_group_roles():
    qs = _query_service(assignments=[_direct(roles=["PLATFORM_USER"])], grants=[_grant(G_USERS, "PROGRAM_OWNER")])
    context = _enrich(qs, _context([G_USERS, G_PLATFORM]))

    assert sorted(context.roles) == ["ADMIN", "PLATFORM_USER", "PROGRAM_OWNER"]


def test_platform_admin_counts_as_admin_without_a_project_in_the_path():
    context = _enrich(_query_service(), _context([G_PLATFORM]), resource_ids={}, resource_path="/projects")

    admin_assignments = [a for a in context.project_assignments if "ADMIN" in a.roles]
    assert [a.projectId for a in admin_assignments] == [authorizer_steps.PLATFORM_ADMIN_PROJECT_ID]


def test_other_groups_are_no_platform_admins():
    context = _enrich(_query_service(), _context([G_USERS]))

    assert context.roles == []


def test_authorizer_passes_the_groups_on():
    step = mock.Mock()

    def invoke(request, context):
        context.user_name = "user-1"
        context.trusted_group_ids = [G_USERS]
        return True

    step.invoke.side_effect = invoke
    result = authorizer.Authorizer(
        logger=mock.Mock(),
        metrics=mock.Mock(),
        api_config_provider=lambda _: authorizer.APIAuthConfig(api_id="api"),
        authorization_steps=[step],
    ).authorize(_request({}))

    assert result["context"]["userGroups"] == f'["{G_USERS}"]'
