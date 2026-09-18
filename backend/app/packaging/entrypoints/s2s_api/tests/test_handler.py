import importlib
import json
from copy import deepcopy
from unittest import mock

import pytest
import yaml
from aws_lambda_powertools.event_handler.exceptions import NotFoundError

from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.exceptions.s2s_exception import ProjectAccessDenied
from app.packaging.domain.model.component import component, component_version
from app.packaging.domain.model.pipeline import pipeline
from app.packaging.domain.model.recipe import recipe, recipe_version
from app.packaging.domain.ports.idempotency_service import Reservation, ReservationOutcome
from app.packaging.entrypoints.s2s_api import idempotency
from app.packaging.entrypoints.s2s_api.model import api_model

IDEMPOTENCY_KEY = "b39cdd55-774d-4bc3-81a8-70f23a03c485"


def load_handler(monkeypatch, dependencies):
    from app.packaging.entrypoints.s2s_api import bootstrapper

    monkeypatch.setattr(bootstrapper, "bootstrap", mock.Mock(return_value=dependencies))
    from app.packaging.entrypoints.s2s_api import handler

    return importlib.reload(handler)


def test_create_component_generates_an_internal_id_and_uses_service_actor(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 201
    component_id = json.loads(response["body"])["componentId"]
    assert component_id.startswith("comp-")
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.componentId.value == component_id
    assert command.createdBy.value == "service:client-1"


def test_recipe_version_model_omits_unavailable_legacy_configured_list():
    from app.packaging.entrypoints.s2s_api.routers import recipes

    version = mock.Mock()
    version.model_dump.return_value = {
        "recipeId": "reci-1",
        "recipeVersionId": "vers-1",
        "recipeComponentsVersions": [
            {
                "componentId": "comp-1",
                "componentName": "agent",
                "componentVersionId": "vers-1",
                "componentVersionName": "1.0.0",
                "componentVersionType": "MAIN",
                "order": 1,
            }
        ],
        "configuredRecipeComponentsVersions": None,
        "recipeVersionDescription": "build",
        "recipeVersionName": "1.0.0-rc.1",
        "recipeVersionVolumeSize": "8",
        "status": "CREATED",
        "createDate": "2025-01-01",
        "createdBy": "T1",
        "lastUpdateDate": "2025-01-01",
        "lastUpdatedBy": "T1",
    }
    result = recipes.recipe_version_model(version).model_dump(exclude_none=True)
    assert "configuredComponentsVersions" not in result
    assert result["effectiveComponentsVersions"]


def test_create_component_version_returns_the_handler_generated_id(
    monkeypatch, mocked_dependencies, lambda_context, client_event, version_body
):
    mocked_dependencies.command_bus.handle.return_value = {"componentVersionId": "vers-1"}
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            version_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 202
    returned_id = json.loads(response["body"])["componentVersionId"]
    assert returned_id.startswith("vers-")
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.componentId.value == "comp-1"
    assert command.createdBy.value == "service:client-1"
    assert command.componentVersionId.value == returned_id


def test_create_component_version_serializes_structured_definition(
    monkeypatch, mocked_dependencies, lambda_context, client_event, version_body
):
    mocked_dependencies.command_bus.handle.return_value = {"componentVersionId": "vers-1"}
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            version_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert response["statusCode"] == 202
    assert yaml.safe_load(command.componentVersionYamlDefinition.value)["phases"][0]["name"] == "build"


@pytest.mark.parametrize(
    "definition_update",
    [
        {"inputs": "scalar"},
        {"constants": [{"Agent": {"type": "number"}}]},
        {"parameters": [{"Agent": {"type": "number"}}]},
    ],
)
def test_create_component_version_rejects_malformed_structured_definition(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    version_body,
    definition_update,
):
    body = deepcopy(version_body)
    body["componentVersionDefinition"].update(definition_update)
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )
    assert response["statusCode"] == 400
    mocked_dependencies.command_bus.handle.assert_not_called()


@pytest.mark.parametrize(
    "inputs",
    [{"commands": ["install-agent"]}, [{"source": "s3://bucket/agent"}]],
)
def test_create_component_version_accepts_object_or_array_inputs(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    version_body,
    inputs,
):
    mocked_dependencies.command_bus.handle.return_value = {"componentVersionId": "vers-1"}
    body = deepcopy(version_body)
    body["componentVersionDefinition"]["phases"][0]["steps"][0]["inputs"] = inputs
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )
    assert response["statusCode"] == 202


def test_component_version_requires_parent_project_membership_before_looking_up_version(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    mocked_dependencies.component_domain_qry_srv.require_component_in_project.side_effect = NotFoundError(
        "Component comp-1 not found in project proj-1."
    )
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "GET",
            "/projects/proj-1/components/comp-1/versions/vers-1",
            scopes=["clients/packaging/component.read"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 404
    mocked_dependencies.component_version_qry_srv.get_component_version.assert_not_called()


def test_component_release_requires_release_scope_before_command(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions/vers-1/release",
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "INSUFFICIENT_SCOPE"
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_retire_component_version_uses_service_authorization(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    mocked_dependencies.command_bus.handle.return_value = {"componentVersionId": "vers-1"}
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "DELETE",
            "/projects/proj-1/components/comp-1/versions/vers-1",
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 202
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.serviceAuthorized is True
    assert command.lastUpdatedBy.value == "service:client-1"


def test_project_denial_happens_before_component_query(monkeypatch, mocked_dependencies, lambda_context, client_event):
    mocked_dependencies.project_access_service.require_access.side_effect = ProjectAccessDenied()
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "GET",
            "/projects/proj-1/components/comp-1",
            scopes=["clients/packaging/component.read"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 403
    assert json.loads(response["body"])["code"] == "PROJECT_ACCESS_DENIED"
    mocked_dependencies.component_domain_qry_srv.require_component_in_project.assert_not_called()


def test_create_recipe_uses_service_actor_and_returns_generated_id(
    monkeypatch, mocked_dependencies, lambda_context, client_event, recipe_body
):
    mocked_dependencies.command_bus.handle.return_value = {"recipeId": "reci-1"}
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "POST",
            "/projects/proj-1/recipes",
            recipe_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/recipe.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 201
    returned_id = json.loads(response["body"])["recipeId"]
    assert returned_id.startswith("reci-")
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.createdBy.value == "service:client-1"
    assert command.recipeId.value == returned_id


def test_create_component_requires_idempotency_key(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["code"] == "INVALID_IDEMPOTENCY_KEY"
    mocked_dependencies.idempotency_service.reserve.assert_not_called()
    mocked_dependencies.command_bus.handle.assert_not_called()


@pytest.mark.parametrize(
    ("path", "body_fixture", "scope"),
    [
        ("/projects/proj-1/components", "component_body", "clients/packaging/component.write"),
        (
            "/projects/proj-1/components/comp-1/versions",
            "version_body",
            "clients/packaging/component.write",
        ),
        ("/projects/proj-1/recipes", "recipe_body", "clients/packaging/recipe.write"),
        (
            "/projects/proj-1/recipes/reci-1/versions",
            "recipe_version_body",
            "clients/packaging/recipe.write",
        ),
        ("/projects/proj-1/pipelines", "pipeline_body", "clients/packaging/pipeline.write"),
    ],
)
@pytest.mark.parametrize("key", [None, "not-a-uuid"])
def test_managed_create_endpoints_require_valid_uuid_idempotency_key(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    request,
    path,
    body_fixture,
    scope,
    key,
):
    headers = {"Idempotency-Key": key} if key is not None else None

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            path,
            request.getfixturevalue(body_fixture),
            headers=headers,
            scopes=[scope],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["code"] == "INVALID_IDEMPOTENCY_KEY"
    mocked_dependencies.idempotency_service.reserve.assert_not_called()


def test_create_component_replays_stored_response(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.REPLAY,
        "comp-fixed",
        201,
        {"componentId": "comp-fixed"},
    )

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 201
    assert json.loads(response["body"]) == {"componentId": "comp-fixed"}
    mocked_dependencies.command_bus.handle.assert_not_called()


@pytest.mark.parametrize(
    ("reservation", "expected_status", "expected_code", "retry_after"),
    [
        (
            Reservation(ReservationOutcome.CONFLICT, "comp-fixed"),
            409,
            "IDEMPOTENCY_KEY_REUSED",
            None,
        ),
        (
            Reservation(ReservationOutcome.IN_PROGRESS, "comp-fixed"),
            409,
            "IDEMPOTENCY_REQUEST_IN_PROGRESS",
            "5",
        ),
    ],
)
def test_create_component_rejects_unavailable_reservation(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    component_body,
    reservation,
    expected_status,
    expected_code,
    retry_after,
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = reservation

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == expected_status
    assert json.loads(response["body"])["code"] == expected_code
    assert response["headers"].get("Retry-After") == retry_after
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_create_component_replays_deterministic_domain_failure_with_fresh_request_id(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.REPLAY,
        "comp-fixed",
        422,
        {
            "detail": "Component name already exists.",
            "code": "DOMAIN_VALIDATION_FAILED",
            "retryable": False,
        },
    )

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 422
    assert body["detail"] == "Component name already exists."
    assert body["requestId"] == "api-request-1"
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_create_component_completes_deterministic_domain_failure(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    mocked_dependencies.command_bus.handle.side_effect = domain_exception.DomainException(
        "Component name already exists."
    )

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 422
    completed = mocked_dependencies.idempotency_service.complete.call_args.args
    assert completed[3:] == (
        422,
        {
            "detail": "Component name already exists.",
            "code": "DOMAIN_VALIDATION_FAILED",
            "retryable": False,
        },
        completed[-1],
    )


def test_create_authorization_failure_happens_before_reservation(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    mocked_dependencies.project_access_service.require_access.side_effect = ProjectAccessDenied()

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 403
    mocked_dependencies.idempotency_service.reserve.assert_not_called()


def test_create_image_does_not_require_idempotency_key(monkeypatch, mocked_dependencies, lambda_context, client_event):
    mocked_dependencies.command_bus.handle.return_value = "imag-1"
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/images",
            {"pipelineId": "pipe-1"},
            scopes=["clients/packaging/pipeline.execute"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 202
    mocked_dependencies.idempotency_service.reserve.assert_not_called()


def test_create_component_rejects_malformed_idempotency_key(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"idempotency-key": "not-a-uuid"},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_create_component_version_injects_reserved_id(
    monkeypatch, mocked_dependencies, lambda_context, client_event, version_body
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.ACQUIRED, "vers-fixed"
    )
    mocked_dependencies.command_bus.handle.return_value = {"componentVersionId": "vers-fixed"}

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            version_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 202
    assert json.loads(response["body"]) == {"componentVersionId": "vers-fixed"}
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.componentVersionId.value == "vers-fixed"
    scope = mocked_dependencies.idempotency_service.reserve.call_args.args[0]
    assert scope.parent_resource_id == "comp-1"


def test_create_component_version_validates_parent_before_reservation(
    monkeypatch, mocked_dependencies, lambda_context, client_event, version_body
):
    mocked_dependencies.component_domain_qry_srv.require_component_in_project.side_effect = NotFoundError(
        "Component missing"
    )

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            version_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 404
    mocked_dependencies.idempotency_service.reserve.assert_not_called()


def test_create_recipe_injects_reserved_id(monkeypatch, mocked_dependencies, lambda_context, client_event, recipe_body):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.ACQUIRED, "reci-fixed"
    )
    mocked_dependencies.command_bus.handle.return_value = {"recipeId": "reci-fixed"}

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/recipes",
            recipe_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/recipe.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 201
    assert json.loads(response["body"]) == {"recipeId": "reci-fixed"}
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.recipeId.value == "reci-fixed"


def test_create_recipe_version_injects_reserved_id(monkeypatch, mocked_dependencies, lambda_context, client_event):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.ACQUIRED, "vers-fixed"
    )
    mocked_dependencies.command_bus.handle.return_value = {"recipeVersionId": "vers-fixed"}
    body = {
        "configuredComponentsVersions": [],
        "recipeVersionDescription": "build image",
        "recipeVersionReleaseType": "MAJOR",
        "recipeVersionVolumeSize": "8",
    }

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/recipes/reci-1/versions",
            body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/recipe.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 202
    assert json.loads(response["body"]) == {"recipeVersionId": "vers-fixed"}
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.recipeVersionId.value == "vers-fixed"


def test_create_pipeline_injects_reserved_id(
    monkeypatch, mocked_dependencies, lambda_context, client_event, pipeline_body
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.ACQUIRED, "pipe-fixed"
    )
    mocked_dependencies.command_bus.handle.return_value = {"pipelineId": "pipe-fixed"}

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/pipelines",
            pipeline_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/pipeline.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 202
    assert json.loads(response["body"]) == {"pipelineId": "pipe-fixed"}
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.pipelineId.value == "pipe-fixed"


def test_archive_component_does_not_redispatch_when_already_archived(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    mocked_dependencies.component_domain_qry_srv.get_component.return_value = mock.Mock(
        status=component.ComponentStatus.Archived
    )

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "DELETE",
            "/projects/proj-1/components/comp-1",
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 200
    mocked_dependencies.command_bus.handle.assert_not_called()


@pytest.mark.parametrize(
    ("method", "path", "scope", "query_name", "status"),
    [
        (
            "DELETE",
            "/projects/proj-1/components/comp-1/versions/vers-1",
            "clients/packaging/component.write",
            "component_version_qry_srv",
            component_version.ComponentVersionStatus.Retired,
        ),
        (
            "POST",
            "/projects/proj-1/components/comp-1/versions/vers-1/release",
            "clients/packaging/component.release",
            "component_version_qry_srv",
            component_version.ComponentVersionStatus.Released,
        ),
        (
            "DELETE",
            "/projects/proj-1/recipes/reci-1/versions/vers-1",
            "clients/packaging/recipe.write",
            "recipe_version_domain_qry_srv",
            recipe_version.RecipeVersionStatus.Retired,
        ),
        (
            "POST",
            "/projects/proj-1/recipes/reci-1/versions/vers-1/release",
            "clients/packaging/recipe.release",
            "recipe_version_domain_qry_srv",
            recipe_version.RecipeVersionStatus.Released,
        ),
        (
            "DELETE",
            "/projects/proj-1/pipelines/pipe-1",
            "clients/packaging/pipeline.write",
            "pipeline_domain_qry_srv",
            pipeline.PipelineStatus.Retired,
        ),
    ],
)
def test_terminal_action_does_not_redispatch(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    method,
    path,
    scope,
    query_name,
    status,
):
    query = getattr(mocked_dependencies, query_name)
    if query_name == "component_version_qry_srv":
        query.get_component_version.return_value = mock.Mock(status=status)
    elif query_name == "recipe_version_domain_qry_srv":
        query.get_recipe_version.return_value = mock.Mock(status=status)
    else:
        query.get_pipeline.return_value = mock.Mock(status=status)

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(method, path, scopes=[scope]), lambda_context
    )

    assert response["statusCode"] in (200, 202)
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_archive_recipe_does_not_redispatch_when_already_archived(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    mocked_dependencies.recipe_domain_qry_srv.get_recipe.return_value = mock.Mock(status=recipe.RecipeStatus.Archived)

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "DELETE",
            "/projects/proj-1/recipes/reci-1",
            scopes=["clients/packaging/recipe.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 200
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_recovery_read_failure_is_retryable_not_ready(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(ReservationOutcome.RECOVER, "comp-fixed")
    mocked_dependencies.component_domain_qry_srv.get_component.side_effect = RuntimeError("dynamodb unavailable")

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 503
    assert body["code"] == "RESOURCE_READ_NOT_READY"
    assert body["retryable"] is True


def test_invalid_stored_component_definition_returns_stable_problem_and_metric(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    raw_version = mock.Mock(componentVersionS3Uri="s3://bucket/key")
    mocked_dependencies.component_version_qry_srv.get_component_version.return_value = raw_version
    mocked_dependencies.component_version_domain_qry_srv.get_component_version.return_value = (
        raw_version,
        {"inputs": "invalid"},
        "unused",
    )
    handler = load_handler(monkeypatch, mocked_dependencies)
    monkeypatch.setattr(
        handler.component_versions.api_model.ComponentVersion,
        "model_validate",
        mock.Mock(return_value=mock.Mock()),
    )
    metric = mock.Mock()
    monkeypatch.setattr(handler.common.api_metrics, "add_metric", metric)

    response = handler.handler(
        client_event(
            "GET",
            "/projects/proj-1/components/comp-1/versions/vers-1",
            scopes=["clients/packaging/component.read"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 500
    assert json.loads(response["body"])["code"] == "STORED_DEFINITION_INVALID"
    assert any(call.kwargs["name"] == "StoredDefinitionParseFailures" for call in metric.call_args_list)


def test_invalid_structured_definition_records_validation_metric(
    monkeypatch, mocked_dependencies, lambda_context, client_event, version_body
):
    version_body["componentVersionDefinition"]["inputs"] = "invalid"
    handler = load_handler(monkeypatch, mocked_dependencies)
    metric = mock.Mock()
    monkeypatch.setattr(handler.common.api_metrics, "add_metric", metric)

    response = handler.handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            version_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 400
    assert any(call.kwargs["name"] == "StructuredDefinitionValidationFailures" for call in metric.call_args_list)


def test_invalid_structured_definition_is_not_logged(
    monkeypatch, mocked_dependencies, lambda_context, client_event, version_body
):
    version_body["componentVersionDefinition"] = "TOP_SECRET_DEFINITION"
    handler = load_handler(monkeypatch, mocked_dependencies)
    log = mock.Mock()
    monkeypatch.setattr(handler.logger, "info", log)

    response = handler.handler(
        client_event(
            "POST",
            "/projects/proj-1/components/comp-1/versions",
            version_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 400
    assert "TOP_SECRET_DEFINITION" not in str(log.call_args_list)


def test_expired_record_emits_expiry_metric_and_logs_only_key_digest(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.ACQUIRED, "comp-fixed", replaced_expired=True
    )
    handler = load_handler(monkeypatch, mocked_dependencies)
    metric = mock.Mock()
    log = mock.Mock()
    monkeypatch.setattr(handler.components.idempotency.metrics, "add_metric", metric)
    monkeypatch.setattr(handler.components.idempotency.logger, "info", log)

    response = handler.handler(
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 201
    metric_names = [call.kwargs["name"] for call in metric.call_args_list]
    assert metric_names == ["IdempotencyReservations", "IdempotencyRecordExpiries"]
    logged = log.call_args.kwargs
    assert IDEMPOTENCY_KEY not in json.dumps(logged)
    assert set(logged) == {
        "operation",
        "projectId",
        "clientId",
        "idempotencyKeyDigest",
        "generatedResourceId",
        "outcome",
    }


def test_canonical_request_hash_includes_defaults_and_retains_array_order(pipeline_body):
    request = api_model.CreatePipelineRequest.model_validate(pipeline_body)
    reversed_request = request.model_copy(update={"buildInstanceTypes": ["m7i.large", "t3.large"]})

    assert idempotency.canonical_request_hash(request) == (
        "429a8e73ac14ba9e79db983d25f1db5c04fa3c30cd0c67fa58966bb3be509bf6"
    )
    assert idempotency.canonical_request_hash(request) != idempotency.canonical_request_hash(reversed_request)
