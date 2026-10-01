"""The authorizer carries the project's remote-support setting into Cedar."""

from unittest import mock

from app.authorization.domain.ports import assignments_query_service
from app.authorization.domain.read_models import project_settings
from app.authorization.domain.services.auth import authorizer, authorizer_steps


def _request(resource_ids: dict, resource_path: str) -> authorizer.AuthorizationRequest:
    return authorizer.AuthorizationRequest(
        auth_token="t",
        api_id="api",
        operation_id="StartSupportSession",
        resource_ids=resource_ids,
        resource_path=resource_path,
        resource="r",
    )


def _context() -> authorizer.AuthorizationContext:
    return authorizer.AuthorizationContext(
        user_name="USER-1",
        api_auth_cfg=authorizer.APIAuthConfig(api_id="api", bounded_context="provisioning"),
        project_scoped_bounded_contexts=["provisioning"],
    )


def _query_service(remote_support_enabled: bool):
    qs = mock.create_autospec(spec=assignments_query_service.AssignmentsQueryService, instance=True)
    qs.get_user_assignments.return_value = []
    qs.get_project_settings.return_value = project_settings.ProjectSettings(
        projectId="proj-1", remoteSupportEnabled=remote_support_enabled
    )
    return qs


def test_enricher_reads_the_setting_of_the_project_in_the_path():
    qs = _query_service(remote_support_enabled=False)
    context = _context()

    assert authorizer_steps.ProjectsBCContextEnricher(assignments_query_service=qs).invoke(
        _request({"projectId": "proj-1"}, "/projects/{projectId}/products/provisioned/{id}/support"), context
    )

    qs.get_project_settings.assert_called_once_with(project_id="proj-1")
    assert context.remote_support_enabled is False


def test_enricher_keeps_the_default_without_a_project_in_the_path():
    qs = _query_service(remote_support_enabled=False)
    context = _context()

    authorizer_steps.ProjectsBCContextEnricher(assignments_query_service=qs).invoke(_request({}, "/profile"), context)

    qs.get_project_settings.assert_not_called()
    assert context.remote_support_enabled is True


def test_resolver_sends_the_setting_on_the_project_entity():
    context = _context()
    context.remote_support_enabled = False
    resolved = authorizer_steps.AVPEntityResolutionContext()

    authorizer_steps.VEWProjectAssignmentEntityResolver().resolve(
        request=_request({"projectId": "proj-1"}, "/projects/{projectId}"), context=context, avp_entities=resolved
    )

    project = next(e for e in resolved.entities if e.identifier.entity_type == authorizer_steps.AVPEntityType.PROJECT)
    assert project.attributes["remoteSupportEnabled"] == {"boolean": False}
