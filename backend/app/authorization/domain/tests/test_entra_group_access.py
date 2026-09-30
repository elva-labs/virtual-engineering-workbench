from unittest.mock import Mock

import pytest

from app.shared.identity.entra_groups import group_ids
from app.authorization.domain.read_models.project_assignment import Assignment
from app.authorization.domain.read_models.project_group_assignment import (
    GroupAssignment,
)
from app.authorization.domain.services.auth import authorizer, authorizer_steps

GROUP = "12345678-1234-1234-1234-123456789abc"


@pytest.mark.parametrize(
    "claim,expected",
    [
        (None, []),
        ("malformed", []),
        ([GROUP, "bad"], []),
        ([GROUP.upper(), GROUP], [GROUP]),
        ('["' + GROUP + '"]', [GROUP]),
        ("[" + GROUP + "," + GROUP.upper() + "]", [GROUP]),
    ],
)
def test_trusted_claim_normalization(claim, expected):
    assert group_ids({"custom:entra_groups": claim}) == expected
    assert group_ids({"groups": [GROUP]}) == []


def test_union_drives_cedar_and_refresh_preserves_direct_grant():
    query = Mock()
    query.get_user_assignments.return_value = [Assignment(userId="USER", projectId="p1", roles=["PLATFORM_USER"])]
    query.get_group_assignments.return_value = [
        GroupAssignment(groupId=GROUP, projectId="p1", roles=["ADMIN"], version=1),
        GroupAssignment(groupId=GROUP, projectId="p2", roles=["ADMIN"], version=1),
        GroupAssignment(groupId=GROUP, projectId="p3", roles=["ADMIN"], version=2, isDeleted=True),
    ]
    request = authorizer.AuthorizationRequest(
        auth_token="Bearer token",
        api_id="api",
        operation_id="GetProject",
        resource_ids={"projectId": "p1"},
        resource_path="/projects/p1",
        resource="arn",
    )
    context = authorizer.AuthorizationContext(
        user_name="USER",
        trusted_group_ids=[GROUP],
        api_auth_cfg=authorizer.APIAuthConfig(bounded_context="projects"),
        project_scoped_bounded_contexts=["projects"],
    )
    step = authorizer_steps.ProjectsBCContextEnricher(query)
    assert step.invoke(request, context)
    assert context.roles == ["ADMIN", "PLATFORM_USER"]
    entities = authorizer_steps.AVPEntityResolutionContext(
        entities=[
            authorizer_steps.AVPEntity(
                identifier=authorizer_steps.AVPEntityIdentifier(entityId="USER", entityType="VEW::User")
            )
        ]
    )
    authorizer_steps.VEWProjectAssignmentEntityResolver().resolve(request, context, entities)
    user = next(e for e in entities.entities if e.identifier.entity_type == "VEW::User")
    assert user.attributes["totalAdminAssignments"] == {"long": 2}
    assert {p.entity_id for p in user.parents} == {
        "p1#ADMIN",
        "p1#PLATFORM_USER",
        "p2#ADMIN",
    }
    context.trusted_group_ids = []
    assert step.invoke(request, context)
    assert context.roles == ["PLATFORM_USER"]
    assert [a.projectId for a in context.project_assignments] == ["p1"]
    query.get_group_assignments.assert_called_once_with([GROUP])


def test_reconciliation_pages_include_revocation_records():
    from types import SimpleNamespace
    from unittest.mock import patch
    from app.authorization.domain.command_handlers import sync_group_assignments_command_handler as sync

    query = Mock()
    query.get_projects.side_effect = [
        SimpleNamespace(items=[SimpleNamespace(projectId="p1")], page_token="next"),
        SimpleNamespace(items=[SimpleNamespace(projectId="p2")], page_token=None),
    ]
    record = GroupAssignment(groupId=GROUP, projectId="p1", roles=[], version=4, isDeleted=True)
    query.get_project_group_assignments.side_effect = [[record], []]
    with patch.object(sync.project_group_assignment_changed_handler, "handle") as handler:
        sync.handle(query, Mock())
        assert handler.call_args.args[0].isDeleted
        assert handler.call_args.args[0].version == 4
    assert query.get_projects.call_args_list[1].args[0].page_token == "next"


def test_human_authorizer_cache_cannot_outlive_token():
    from pathlib import Path
    import yaml

    for schema_path in Path("app").glob("*/entrypoints/api/schema/*.yaml"):
        schema = yaml.safe_load(schema_path.read_text())
        definitions = schema.get("securityDefinitions", {})
        definitions.update(schema.get("components", {}).get("securitySchemes", {}))
        for definition in definitions.values():
            auth = definition.get("x-amazon-apigateway-authorizer", {})
            if "authorizerResultTtlInSeconds" in auth:
                assert auth["authorizerResultTtlInSeconds"] == 0, schema_path


def test_group_only_stage_access_uses_same_effective_roles():
    query = Mock()
    query.get_user_assignments.return_value = []
    query.get_group_assignments.return_value = [
        GroupAssignment(groupId=GROUP, projectId="p", roles=["PLATFORM_USER"], version=1)]
    class Identity(authorizer.AuthorizerStep):
        def invoke(self, request, context):
            context.user_name = "USER"
            context.user_email = "user@example.test"
            context.trusted_group_ids = [GROUP]
            return True
    service = authorizer.Authorizer(logger=Mock(), metrics=Mock(),
        api_config_provider=lambda api: authorizer.APIAuthConfig(bounded_context="projects"),
        project_scoped_bounded_contexts=["projects"],
        authorization_steps=[Identity(), authorizer_steps.ProjectsBCContextEnricher(query)],
        stage_access_config={"PLATFORM_USER": ["dev", "test"]})
    result = service.authorize(authorizer.AuthorizationRequest(auth_token="Bearer token", api_id="api", operation_id="GetProject",
        resource_ids={"projectId":"p"}, resource_path="/projects/p", resource="arn"))
    import json
    assert json.loads(result["context"]["stages"]) == ["dev", "test"]
    assert json.loads(result["context"]["userRoles"]) == ["PLATFORM_USER"]
    assert "trusted_group_ids" not in result["context"]
