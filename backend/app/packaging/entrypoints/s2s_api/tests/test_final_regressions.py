import json
from unittest import mock

import pytest

from app.packaging.domain.exceptions.domain_exception import DomainException
from app.packaging.domain.model.component.component_version import ComponentVersion
from app.packaging.domain.model.recipe.recipe_version import RecipeVersion
from app.packaging.domain.model.shared.component_version_entry import ComponentVersionEntry
from app.packaging.domain.value_objects.component_version import component_version_yaml_definition_value_object
from app.packaging.entrypoints.s2s_api.tests.test_handler import IDEMPOTENCY_KEY, load_handler


@pytest.fixture()
def recipe_version_entity():
    user = ComponentVersionEntry(
        componentId="comp-user",
        componentName="User",
        componentVersionId="vers-user",
        componentVersionName="1.0.0",
        componentVersionType="MAIN",
        order=1,
    )
    mandatory = ComponentVersionEntry(
        componentId="comp-mandatory",
        componentName="Mandatory",
        componentVersionId="vers-mandatory",
        componentVersionName="1.0.0",
        componentVersionType="HELPER",
        order=1,
        position="PREPEND",
    )
    return RecipeVersion(
        recipeId="reci-1",
        recipeVersionId="vers-1",
        parentImageUpstreamId="ami-1",
        configuredRecipeComponentsVersions=[user],
        recipeComponentsVersions=[mandatory, user.model_copy(update={"order": 2})],
        recipeName="Recipe",
        recipeVersionDescription="Build",
        recipeVersionName="1.0.0-rc.1",
        recipeVersionVolumeSize="8",
        status="CREATED",
        createDate="2026-09-18",
        createdBy="actor",
        lastUpdateDate="2026-09-18",
        lastUpdatedBy="actor",
    )


@pytest.mark.parametrize("listing", [False, True], ids=["get", "list"])
@pytest.mark.parametrize("legacy", [False, True], ids=["configured", "legacy"])
def test_recipe_reads_project_real_entries_and_omit_unavailable_configuration(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    recipe_version_entity,
    listing,
    legacy,
):
    if legacy:
        recipe_version_entity.configuredRecipeComponentsVersions = None
    query = mocked_dependencies.recipe_version_domain_qry_srv
    query.get_recipe_version.return_value = recipe_version_entity
    query.get_recipe_versions.return_value = [recipe_version_entity]
    path = "/projects/proj-1/recipes/reci-1/versions" + ("" if listing else "/vers-1")

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event("GET", path, scopes=["clients/packaging/recipe.read"]),
        lambda_context,
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    version = body["recipe_versions"][0] if listing else body["recipe_version"]
    effective = version["effectiveComponentsVersions"]
    assert [entry["componentId"] for entry in effective] == ["comp-mandatory", "comp-user"]
    assert [entry["order"] for entry in effective] == [1, 2]
    assert all("position" not in entry for entry in effective)
    if legacy:
        assert "configuredComponentsVersions" not in version
    else:
        assert version["configuredComponentsVersions"] == [
            {
                "componentId": "comp-user",
                "componentName": "User",
                "componentVersionId": "vers-user",
                "componentVersionName": "1.0.0",
                "componentVersionType": "MAIN",
                "order": 1,
            }
        ]


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize("invalid", ["phases", "steps", "extra", "missing", "conversion"])
def test_invalid_component_definition_has_safe_dedicated_problem(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    version_body,
    method,
    invalid,
    caplog,
):
    body = version_body
    if method == "PUT":
        body.pop("componentVersionReleaseType")
    definition = body["componentVersionDefinition"]
    if invalid == "phases":
        definition["phases"] = []
    elif invalid == "steps":
        definition["phases"][0]["steps"] = []
    elif invalid == "extra":
        definition["SENSITIVE_REJECTED_INPUT"] = "SENSITIVE_REJECTED_INPUT"
    elif invalid == "missing":
        body.pop("componentVersionDefinition")
    else:
        monkeypatch.setattr(
            component_version_yaml_definition_value_object,
            "from_dict",
            mock.Mock(side_effect=DomainException("SENSITIVE_REJECTED_INPUT")),
        )
    handler = load_handler(monkeypatch, mocked_dependencies)
    metric = mock.Mock(wraps=handler.common.api_metrics.add_metric)
    monkeypatch.setattr(handler.common.api_metrics, "add_metric", metric)
    path = "/projects/proj-1/components/comp-1/versions" + ("/vers-1" if method == "PUT" else "")

    response = handler.handler(
        client_event(method, path, body, headers={"Idempotency-Key": IDEMPOTENCY_KEY}), lambda_context
    )

    assert response["statusCode"] == 422
    problem = json.loads(response["body"])
    assert problem["code"] == "INVALID_COMPONENT_DEFINITION"
    assert problem["retryable"] is False
    assert "SENSITIVE_REJECTED_INPUT" not in response["body"] + caplog.text
    assert any(call.kwargs["name"] == "StructuredDefinitionValidationFailures" for call in metric.call_args_list)
    mocked_dependencies.command_bus.handle.assert_not_called()
    mocked_dependencies.idempotency_service.reserve.assert_not_called()


def test_get_component_version_coerces_numeric_stored_schema_version(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
):
    version = ComponentVersion(
        componentId="comp-1",
        componentVersionId="vers-1",
        componentName="Agent",
        componentVersionName="1.0.0",
        componentVersionDescription="Agent",
        componentVersionS3Uri="s3://bucket/key",
        componentPlatform="Linux",
        componentSupportedArchitectures=["amd64"],
        componentSupportedOsVersions=["Ubuntu 24"],
        softwareVendor="Example",
        softwareVersion="1.0.0",
        status="VALIDATED",
        createDate="2026-09-18",
        createdBy="actor",
        lastUpdateDate="2026-09-18",
        lastUpdatedBy="actor",
    )
    definition = {
        "schemaVersion": 1.0,
        "phases": [
            {
                "name": "build",
                "steps": [{"name": "InstallAgent", "action": "ExecuteBash"}],
            }
        ],
    }
    mocked_dependencies.component_version_qry_srv.get_component_version.return_value = version
    mocked_dependencies.component_version_domain_qry_srv.get_component_version.return_value = (
        version,
        definition,
        "unused",
    )

    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event("GET", "/projects/proj-1/components/comp-1/versions/vers-1"),
        lambda_context,
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["componentVersionDefinition"]["schemaVersion"] == "1.0"


def test_component_command_response_is_not_captured_by_tracing_provider(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
    version_body,
):
    version = ComponentVersion(
        componentId="comp-1",
        componentVersionId="vers-1",
        componentName="Agent",
        componentVersionName="1.0.0",
        componentVersionDescription="Agent",
        componentVersionS3Uri="s3://bucket/key",
        componentPlatform="Linux",
        componentSupportedArchitectures=["amd64"],
        componentSupportedOsVersions=["Ubuntu 24"],
        softwareVendor="Example",
        softwareVersion="1.0.0",
        status="VALIDATED",
        createDate="2026-09-18",
        createdBy="actor",
        lastUpdateDate="2026-09-18",
        lastUpdatedBy="actor",
    )
    definition = version_body["componentVersionDefinition"]
    definition["phases"][0]["steps"][0]["inputs"]["commands"] = ["SECRET_COMPONENT_COMMAND"]
    mocked_dependencies.component_version_qry_srv.get_component_version.return_value = version
    mocked_dependencies.component_version_domain_qry_srv.get_component_version.return_value = (
        version,
        definition,
        "unused",
    )
    handler = load_handler(monkeypatch, mocked_dependencies)
    provider = mock.MagicMock()
    monkeypatch.setattr(handler.tracer, "provider", provider)
    monkeypatch.setattr(handler.component_versions.tracer, "provider", provider)

    response = handler.handler(
        client_event("GET", "/projects/proj-1/components/comp-1/versions/vers-1"), lambda_context
    )

    assert response["statusCode"] == 200
    assert "SECRET_COMPONENT_COMMAND" in response["body"]
    subsegment = provider.in_subsegment.return_value.__enter__.return_value
    assert "SECRET_COMPONENT_COMMAND" not in str(subsegment.put_metadata.call_args_list)
    subsegment.put_metadata.assert_not_called()


def test_escaping_exception_is_not_captured_as_tracing_metadata(
    monkeypatch,
    mocked_dependencies,
    lambda_context,
    client_event,
):
    handler = load_handler(monkeypatch, mocked_dependencies)
    provider = mock.MagicMock()
    monkeypatch.setattr(handler.tracer, "provider", provider)
    monkeypatch.setattr(handler, "append_correlation_fields", mock.Mock(side_effect=RuntimeError("SECRET_EXCEPTION")))

    with pytest.raises(RuntimeError, match="SECRET_EXCEPTION"):
        handler.handler(client_event("GET", "/projects/proj-1/components"), lambda_context)

    subsegment = provider.in_subsegment.return_value.__enter__.return_value
    assert "SECRET_EXCEPTION" not in str(subsegment.put_metadata.call_args_list)
    subsegment.put_metadata.assert_not_called()
