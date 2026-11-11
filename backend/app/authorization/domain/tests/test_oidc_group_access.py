import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.authorization.domain.read_models.project_assignment import Assignment
from app.authorization.domain.read_models.project_group_assignment import GroupAssignment
from app.authorization.domain.services.auth import authorizer, authorizer_steps
from app.shared.identity import oidc_groups

GROUP = "engineering/team"
PROVIDER = "ExampleOIDC"


@pytest.mark.parametrize(
    "claim,expected",
    [
        (None, []),
        ([], []),
        ("[]", []),
        ([GROUP, GROUP, "Other"], ["Other", GROUP]),
        (json.dumps([GROUP, "group,with spaces", "%literal"]), ["%literal", GROUP, "group,with spaces"]),
        ("[engineering%2Fteam,group%2Cwith%20spaces]", [GROUP, "group,with spaces"]),
        ("[Case,case]", ["Case", "case"]),
        ("not-a-uuid", ["not-a-uuid"]),
        ("123", ["123"]),
        ("true", ["true"]),
        ("null", ["null"]),
        ([GROUP, 3], []),
        ({"groups": [GROUP]}, []),
    ],
)
def test_group_identifiers_are_opaque_strings(claim, expected):
    assert oidc_groups.group_ids(claim) == expected


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "identities", [None, "invalid", '[{"providerName":"Other"}]', json.dumps([{"providerName": PROVIDER}])]
)
def test_authorizer_requires_opt_in_and_linked_provider(monkeypatch, enabled, identities):
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER if enabled else "")
    auth_service = Mock()
    auth_service.get_user_info.return_value = {
        "email": "user@example.test",
        "custom:user_tid": "USER",
        oidc_groups.GROUPS_ATTRIBUTE: [GROUP],
        "identities": identities,
    }
    request = _request()
    context = authorizer.AuthorizationContext(
        api_auth_cfg=authorizer.APIAuthConfig(bounded_context="projects"), project_scoped_bounded_contexts=["projects"]
    )
    query = Mock()
    query.get_user_assignments.return_value = [Assignment(userId="USER", projectId="p1", roles=["PLATFORM_USER"])]
    query.get_group_assignments.return_value = [
        GroupAssignment(groupId=GROUP, projectId="p1", roles=["ADMIN"], version=1)
    ]
    assert authorizer_steps.CognitoAuthorizer(auth_service, Mock(), Mock()).invoke(request, context)
    assert authorizer_steps.ProjectsBCContextEnricher(query).invoke(request, context)
    granted = enabled and identities == json.dumps([{"providerName": PROVIDER}])
    assert context.roles == (["ADMIN", "PLATFORM_USER"] if granted else ["PLATFORM_USER"])
    if not granted:
        query.get_group_assignments.assert_not_called()


def _request():
    return authorizer.AuthorizationRequest(
        auth_token="Bearer token",
        api_id="api",
        operation_id="GetProject",
        resource_ids={"projectId": "p1"},
        resource_path="/projects/p1",
        resource="arn",
    )


def test_unset_setting_keeps_direct_roles_and_ignores_stale_context(monkeypatch):
    monkeypatch.delenv("OIDC_GROUPS_PROVIDER_NAME", raising=False)
    assert (
        oidc_groups.trusted_group_ids(
            {"identities": [{"providerName": PROVIDER}], oidc_groups.GROUPS_ATTRIBUTE: [GROUP]}
        )
        == []
    )
    query = Mock()
    direct = Assignment(userId="USER", projectId="p1", roles=["PLATFORM_USER"])
    query.get_user_assignments.return_value = [direct]
    context = authorizer.AuthorizationContext(
        user_name="USER",
        trusted_group_ids=[GROUP],
        api_auth_cfg=authorizer.APIAuthConfig(bounded_context="projects"),
        project_scoped_bounded_contexts=["projects"],
    )
    assert authorizer_steps.ProjectsBCContextEnricher(query).invoke(_request(), context)
    assert context.project_assignments == [direct]
    query.get_group_assignments.assert_not_called()


def test_union_drives_cedar_and_refresh_preserves_direct_grant(monkeypatch):
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    query = Mock()
    query.get_user_assignments.return_value = [Assignment(userId="USER", projectId="p1", roles=["PLATFORM_USER"])]
    query.get_group_assignments.return_value = [
        GroupAssignment(groupId=GROUP, projectId="p1", roles=["ADMIN"], version=1),
        GroupAssignment(groupId=GROUP, projectId="p2", roles=["ADMIN"], version=1),
        GroupAssignment(groupId=GROUP, projectId="p3", roles=["ADMIN"], version=2, isDeleted=True),
    ]
    context = authorizer.AuthorizationContext(
        user_name="USER",
        trusted_group_ids=[GROUP],
        api_auth_cfg=authorizer.APIAuthConfig(bounded_context="projects"),
        project_scoped_bounded_contexts=["projects"],
    )
    step = authorizer_steps.ProjectsBCContextEnricher(query)
    assert step.invoke(_request(), context)
    assert context.roles == ["ADMIN", "PLATFORM_USER"]
    entities = authorizer_steps.AVPEntityResolutionContext(
        entities=[
            authorizer_steps.AVPEntity(
                identifier=authorizer_steps.AVPEntityIdentifier(entityId="USER", entityType="VEW::User")
            )
        ]
    )
    authorizer_steps.VEWProjectAssignmentEntityResolver().resolve(_request(), context, entities)
    user = next(e for e in entities.entities if e.identifier.entity_type == "VEW::User")
    assert user.attributes["totalAdminAssignments"] == {"long": 2}
    assert {p.entity_id for p in user.parents} == {"p1#ADMIN", "p1#PLATFORM_USER", "p2#ADMIN"}
    context.trusted_group_ids = []
    assert step.invoke(_request(), context)
    assert context.roles == ["PLATFORM_USER"]
    assert [a.projectId for a in context.project_assignments] == ["p1"]


def test_reconciliation_pages_include_revocations():
    from app.authorization.domain.command_handlers import sync_group_assignments_command_handler as sync

    query = Mock()
    query.get_projects.side_effect = [
        SimpleNamespace(items=[SimpleNamespace(projectId="p1")], page_token="next"),
        SimpleNamespace(items=[SimpleNamespace(projectId="p2")], page_token=None),
    ]
    query.get_project_group_assignments.side_effect = [
        [GroupAssignment(groupId=GROUP, projectId="p1", roles=[], version=4, isDeleted=True)],
        [],
    ]
    with patch.object(sync.project_group_assignment_changed_handler, "handle") as handler:
        sync.handle(query, Mock())
        assert handler.call_args.args[0].isDeleted
        assert handler.call_args.args[0].version == 4
    assert query.get_projects.call_args_list[1].args[0].page_token == "next"


def test_group_only_stage_access_uses_effective_roles(monkeypatch):
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", PROVIDER)
    query = Mock()
    query.get_user_assignments.return_value = []
    query.get_group_assignments.return_value = [
        GroupAssignment(groupId=GROUP, projectId="p1", roles=["PLATFORM_USER"], version=1)
    ]

    class Identity(authorizer.AuthorizerStep):
        def invoke(self, request, context):
            context.user_name = "USER"
            context.user_email = "user@example.test"
            context.trusted_group_ids = [GROUP]
            return True

    service = authorizer.Authorizer(
        logger=Mock(),
        metrics=Mock(),
        api_config_provider=lambda api: authorizer.APIAuthConfig(bounded_context="projects"),
        project_scoped_bounded_contexts=["projects"],
        authorization_steps=[Identity(), authorizer_steps.ProjectsBCContextEnricher(query)],
        stage_access_config={"PLATFORM_USER": ["dev", "test"]},
    )
    result = service.authorize(_request())
    assert json.loads(result["context"]["stages"]) == ["dev", "test"]
    assert json.loads(result["context"]["userRoles"]) == ["PLATFORM_USER"]
    assert "trusted_group_ids" not in result["context"]


def test_group_query_reads_all_pages_and_deduplicates_ids():
    from app.authorization.adapters.query_services.assignments_dynamodb_query_service import (
        AssignmentsDynamoDBQueryService,
    )

    client = Mock()
    records = [
        GroupAssignment(groupId=GROUP, projectId=p, roles=["PLATFORM_USER"], version=1).model_dump()
        for p in ["p1", "p2"]
    ]
    client.get_paginator.return_value.paginate.return_value = [{"Items": [r]} for r in records]
    query = AssignmentsDynamoDBQueryService("table", client, "inverted")
    assert [a.projectId for a in query.get_group_assignments([GROUP, GROUP])] == ["p1", "p2"]
    client.get_paginator.return_value.paginate.assert_called_once()
    assert client.get_paginator.return_value.paginate.call_args.kwargs["ConsistentRead"]
