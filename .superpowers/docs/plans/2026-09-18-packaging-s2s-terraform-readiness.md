# Packaging S2S Terraform Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Packaging S2S API for a future Terraform provider by adding safe create retries, structured component definitions, declarative recipe component state, and deterministic lifecycle polling.

**Architecture:** Keep `/clients/packaging/v1` as a thin adapter over the existing Packaging domain. Add one DynamoDB-backed idempotency service at the S2S boundary, inject pre-generated IDs into existing create commands, and retain resource-status polling. Store configured recipe components separately from the effective list and translate structured component definitions to the existing YAML-based domain events.

**Tech Stack:** Python 3.13, Pydantic 2, AWS Lambda Powertools, DynamoDB, AWS CDK, OpenAPI 3.0.1, pytest, moto, datamodel-code-generator.

**Spec:** `.superpowers/docs/specs/2026-09-18-packaging-s2s-terraform-readiness-design.md`

## Global Constraints

- Change only the Packaging S2S contract; the user API and UI YAML behavior remain unchanged.
- Keep `/clients/packaging/v1`; do not add provider code or image-build idempotency.
- Require RFC 4122 UUID `Idempotency-Key` values on component, component-version, recipe, recipe-version, and pipeline creates.
- Use a 60-second lease and a 24-hour completed-record lifetime through DynamoDB `ExpireDate` TTL.
- Preserve phase, step, configured-component, and effective-component ordering.
- Keep existing internal IDs as the automation identities.
- Work test-first and commit only the files belonging to each task.

---

### Task 1: Structured Component Definition Contract

**Files:**
- Modify: `backend/app/packaging/domain/value_objects/component_version/component_version_yaml_definition_value_object.py`
- Modify: `backend/app/packaging/domain/tests/value_objects/test_component_version_value_objects.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/schema/proserve-workbench-s2s-packaging-api-schema.yaml`
- Regenerate: `backend/app/packaging/entrypoints/s2s_api/model/api_model.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/routers/component_versions.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/conftest.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_handler.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_schema.py`

**Interfaces:**
- Produces: `from_dict(value: dict) -> ComponentVersionYamlDefinitionValueObject`.
- Produces: `to_dict(value: str | bytes) -> dict`.
- Produces: S2S field `componentVersionDefinition` on create, update, and read.
- Preserves: `componentVersionYamlDefinition` inside domain commands and events.

- [ ] **Step 1: Add failing value-object tests**

```python
def test_component_definition_dict_round_trips_with_defaults():
    definition = {
        "schemaVersion": "1.0",
        "phases": [{
            "name": "build",
            "steps": [{
                "name": "InstallAgent",
                "action": "ExecuteBash",
                "inputs": {"commands": ["install-agent"]},
            }],
        }],
    }
    encoded = component_version_yaml_definition_value_object.from_dict(definition)
    decoded = component_version_yaml_definition_value_object.to_dict(encoded.value)
    step = decoded["phases"][0]["steps"][0]
    assert step["timeoutSeconds"] == 7200
    assert step["onFailure"] == "Abort"
    assert step["maxAttempts"] == 1


def test_component_definition_rejects_empty_phases():
    with pytest.raises(domain_exception.DomainException):
        component_version_yaml_definition_value_object.from_dict(
            {"schemaVersion": "1.0", "phases": []}
        )
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd backend && uv run pytest app/packaging/domain/tests/value_objects/test_component_version_value_objects.py -q`

Expected: FAIL because `from_dict` and `to_dict` do not exist.

- [ ] **Step 3: Implement canonical conversion**

```python
class ComponentPhaseYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["build", "test", "validate"]
    steps: Annotated[List[ComponentStepYaml], Field(min_length=1)]


class ComponentYaml(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)
    name: Optional[str] = None
    description: Optional[str] = None
    schemaVersion: str
    constants: Optional[List[Dict[str, ComponentConstantYaml]]] = None
    parameters: Optional[List[Dict[str, ComponentParameterYaml]]] = None
    phases: Annotated[List[ComponentPhaseYaml], Field(min_length=1)]


def from_dict(value: dict) -> ComponentVersionYamlDefinitionValueObject:
    try:
        model = ComponentYaml.model_validate(value)
    except ValidationError as error:
        raise domain_exception.DomainException("Component version definition is invalid.") from error
    canonical = model.model_dump(mode="json", exclude_none=True)
    return ComponentVersionYamlDefinitionValueObject(
        value=yaml.safe_dump(canonical, sort_keys=False)
    )


def to_dict(value: str | bytes) -> dict:
    try:
        model = ComponentYaml.model_validate(yaml.safe_load(value))
    except (TypeError, yaml.YAMLError, ValidationError) as error:
        raise domain_exception.DomainException("Component version definition is invalid.") from error
    return model.model_dump(mode="json", exclude_none=True)
```

Keep `from_str` for the user API and validate it through `to_dict` while returning the original YAML text.

- [ ] **Step 4: Add failing S2S contract tests**

```python
def test_schema_uses_structured_component_definitions(api_schema):
    schemas = api_schema["components"]["schemas"]
    create = schemas["CreateComponentVersionRequest"]
    response = schemas["ComponentVersionResponse"]
    assert create["properties"]["componentVersionDefinition"]["$ref"] == "#/components/schemas/ComponentDefinition"
    assert "componentVersionYamlDefinition" not in create["properties"]
    assert "yaml_definition" not in response["properties"]
    assert "yaml_definition_b64" not in response["properties"]


def test_create_component_version_serializes_structured_definition(
    monkeypatch, mocked_dependencies, lambda_context, client_event, version_body
):
    mocked_dependencies.command_bus.handle.return_value = {"componentVersionId": "vers-1"}
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event("POST", "/projects/proj-1/components/comp-1/versions", version_body,
                     scopes=["clients/packaging/component.write"]),
        lambda_context,
    )
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert response["statusCode"] == 202
    assert yaml.safe_load(command.componentVersionYamlDefinition.value)["phases"][0]["name"] == "build"
```

Change `version_body` to contain `componentVersionDefinition` with one build phase and step.

- [ ] **Step 5: Update OpenAPI, regenerate models, and map the router**

Define `ComponentDefinition`, `ComponentPhase`, and `ComponentStep`. Use `additionalProperties: false` for definitions and phases, enums for phase name and `onFailure`, `minItems: 1` for phases and steps, and a JSON-compatible free-form `inputs` value.

```python
definition = request.componentVersionDefinition.model_dump(
    mode="json", by_alias=True, exclude_none=True
)
kwargs["componentVersionYamlDefinition"] = (
    component_version_yaml_definition_value_object.from_dict(definition)
)

return api_model.ComponentVersionResponse(
    component_version=api_model.ComponentVersion.model_validate(version.model_dump()),
    componentVersionDefinition=api_model.ComponentDefinition.model_validate(yaml_definition),
)
```

Regenerate:

```bash
cd backend
uv run datamodel-codegen \
  --input app/packaging/entrypoints/s2s_api/schema/proserve-workbench-s2s-packaging-api-schema.yaml \
  --output app/packaging/entrypoints/s2s_api/model/api_model.py \
  --output-model-type pydantic_v2.BaseModel
uv run black app/packaging/entrypoints/s2s_api/model/api_model.py
```

- [ ] **Step 6: Verify structured-definition behavior**

Run: `cd backend && uv run pytest app/packaging/domain/tests/value_objects/test_component_version_value_objects.py app/packaging/entrypoints/s2s_api/tests/test_schema.py app/packaging/entrypoints/s2s_api/tests/test_handler.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/packaging/domain/value_objects/component_version/component_version_yaml_definition_value_object.py \
  backend/app/packaging/domain/tests/value_objects/test_component_version_value_objects.py \
  backend/app/packaging/entrypoints/s2s_api/schema/proserve-workbench-s2s-packaging-api-schema.yaml \
  backend/app/packaging/entrypoints/s2s_api/model/api_model.py \
  backend/app/packaging/entrypoints/s2s_api/routers/component_versions.py \
  backend/app/packaging/entrypoints/s2s_api/tests/conftest.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_handler.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_schema.py
git commit -m "feat(packaging): structure S2S component definitions"
```

### Task 2: Persist Configured and Effective Recipe Components

**Files:**
- Modify: `backend/app/packaging/domain/model/recipe/recipe_version.py`
- Modify: `backend/app/packaging/domain/command_handlers/recipe/create_recipe_version_command_handler.py`
- Modify: `backend/app/packaging/domain/command_handlers/recipe/update_recipe_version_command_handler.py`
- Modify: `backend/app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py`
- Modify: `backend/app/packaging/domain/tests/recipe/test_update_recipe_version_command_handler.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/schema/proserve-workbench-s2s-packaging-api-schema.yaml`
- Regenerate: `backend/app/packaging/entrypoints/s2s_api/model/api_model.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/routers/recipes.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_handler.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_schema.py`

**Interfaces:**
- Produces internal optional field `configuredRecipeComponentsVersions`.
- Produces S2S request `configuredComponentsVersions`.
- Produces S2S read fields `configuredComponentsVersions` and `effectiveComponentsVersions`.
- Preserves `recipeComponentsVersions` as the effective domain list.

- [ ] **Step 1: Add failing create, update, and legacy tests**

```python
def test_create_persists_configured_components_before_mandatory_injection(
    create_recipe_version_command_mock, handler_dependencies
):
    command = create_recipe_version_command_mock
    configured_ids = [item.componentVersionId for item in command.recipeComponentsVersions.value]
    create_recipe_version_command_handler.handle(command=command, **handler_dependencies)
    saved = handler_dependencies["uow"].get_repository.return_value.add.call_args.args[0]
    assert [item.componentVersionId for item in saved.configuredRecipeComponentsVersions] == configured_ids
    assert len(saved.recipeComponentsVersions) >= len(saved.configuredRecipeComponentsVersions)
```

Add an equivalent update assertion against `update_attributes`. Add a model test that validates a historical item without the new field and asserts the value is `None`.

- [ ] **Step 2: Verify the domain tests fail**

Run: `cd backend && uv run pytest app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py app/packaging/domain/tests/recipe/test_update_recipe_version_command_handler.py -q`

Expected: FAIL because configured state is not persisted.

- [ ] **Step 3: Add optional persistence and deep-copy caller state**

```python
class RecipeVersion(unit_of_work.Entity):
    recipeId: str = Field(..., title="RecipeId")
    recipeVersionId: str = Field(default_factory=generate_version_id, title="RecipeVersionId")
    configuredRecipeComponentsVersions: typing.Optional[
        list[component_version_entry.ComponentVersionEntry]
    ] = Field(None, title="ConfiguredRecipeComponentsVersions")
    recipeComponentsVersions: list[component_version_entry.ComponentVersionEntry] = Field(
        ..., title="RecipeComponentsVersions"
    )
```

In create and update, snapshot the caller list before helper functions rewrite `order`:

```python
configured_components = [
    entry.model_copy(deep=True) for entry in command.recipeComponentsVersions.value
]
```

Persist that snapshot while leaving events and downstream workflows on the effective list.

- [ ] **Step 4: Add failing S2S mapping tests**

```python
def test_schema_separates_recipe_component_views(api_schema):
    schemas = api_schema["components"]["schemas"]
    create = schemas["CreateRecipeVersionRequest"]
    version = schemas["RecipeVersion"]
    assert "configuredComponentsVersions" in create["required"]
    assert "recipeComponentsVersions" not in create["properties"]
    assert set(version["properties"]) >= {
        "configuredComponentsVersions", "effectiveComponentsVersions"
    }


def test_recipe_version_model_omits_unavailable_legacy_configured_list(get_mock_recipe_version):
    get_mock_recipe_version.configuredRecipeComponentsVersions = None
    result = recipes.recipe_version_model(get_mock_recipe_version).model_dump(exclude_none=True)
    assert "configuredComponentsVersions" not in result
    assert result["effectiveComponentsVersions"]
```

- [ ] **Step 5: Update contract and centralize response mapping**

```python
def recipe_version_model(version) -> api_model.RecipeVersion:
    payload = version.model_dump()
    payload["effectiveComponentsVersions"] = payload.pop("recipeComponentsVersions")
    configured = payload.pop("configuredRecipeComponentsVersions", None)
    if configured is not None:
        payload["configuredComponentsVersions"] = configured
    return api_model.RecipeVersion.model_validate(payload)
```

Use this helper for list and single reads. Map the S2S request field to the existing command field. Regenerate `api_model.py` with the Task 1 command.

- [ ] **Step 6: Verify recipe behavior**

Run: `cd backend && uv run pytest app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py app/packaging/domain/tests/recipe/test_update_recipe_version_command_handler.py app/packaging/entrypoints/s2s_api/tests/test_schema.py app/packaging/entrypoints/s2s_api/tests/test_handler.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/packaging/domain/model/recipe/recipe_version.py \
  backend/app/packaging/domain/command_handlers/recipe/create_recipe_version_command_handler.py \
  backend/app/packaging/domain/command_handlers/recipe/update_recipe_version_command_handler.py \
  backend/app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py \
  backend/app/packaging/domain/tests/recipe/test_update_recipe_version_command_handler.py \
  backend/app/packaging/entrypoints/s2s_api/schema/proserve-workbench-s2s-packaging-api-schema.yaml \
  backend/app/packaging/entrypoints/s2s_api/model/api_model.py \
  backend/app/packaging/entrypoints/s2s_api/routers/recipes.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_handler.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_schema.py
git commit -m "feat(packaging): separate configured recipe components"
```

### Task 3: Allow Create Commands to Inject Resource IDs

**Files:**
- Modify: `backend/app/packaging/domain/commands/component/create_component_version_command.py`
- Modify: `backend/app/packaging/domain/commands/recipe/create_recipe_command.py`
- Modify: `backend/app/packaging/domain/commands/recipe/create_recipe_version_command.py`
- Modify: `backend/app/packaging/domain/commands/pipeline/create_pipeline_command.py`
- Modify: `backend/app/packaging/domain/command_handlers/component/create_component_version_command_handler.py`
- Modify: `backend/app/packaging/domain/command_handlers/recipe/create_recipe_command_handler.py`
- Modify: `backend/app/packaging/domain/command_handlers/recipe/create_recipe_version_command_handler.py`
- Modify: `backend/app/packaging/domain/command_handlers/pipeline/create_pipeline_command_handler.py`
- Modify: `backend/app/packaging/domain/tests/component/test_create_component_version_command_handler.py`
- Modify: `backend/app/packaging/domain/tests/recipe/test_create_recipe_command_handler.py`
- Modify: `backend/app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py`
- Modify: `backend/app/packaging/domain/tests/pipeline/test_create_pipeline_command_handler.py`

**Interfaces:**
- Produces optional fields `componentVersionId`, `recipeId`, `recipeVersionId`, and `pipelineId` with existing value-object types.
- Preserves default generation when a field is absent.
- Component creation already requires `componentId` and remains unchanged.

- [ ] **Step 1: Add failing fixed-ID tests**

```python
def test_create_component_version_uses_injected_id(
    create_component_version_command_mock, handler_dependencies
):
    command = create_component_version_command_mock.model_copy(update={
        "componentVersionId": component_version_id_value_object.from_str("vers-fixed")
    })
    result = create_component_version_command_handler.handle(command=command, **handler_dependencies)
    saved = handler_dependencies["uow"].get_repository.return_value.add.call_args.args[0]
    assert result == {"componentVersionId": "vers-fixed"}
    assert saved.componentVersionId == "vers-fixed"
```

Add equivalent tests for `reci-fixed`, recipe `vers-fixed`, and `pipe-fixed`. Retain one generation test per handler.

- [ ] **Step 2: Verify the four suites fail**

Run: `cd backend && uv run pytest app/packaging/domain/tests/component/test_create_component_version_command_handler.py app/packaging/domain/tests/recipe/test_create_recipe_command_handler.py app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py app/packaging/domain/tests/pipeline/test_create_pipeline_command_handler.py -q`

Expected: FAIL because the command models reject injected IDs.

- [ ] **Step 3: Add optional command fields and entity constructor values**

Add `pipelineId: Optional[pipeline_id_value_object.PipelineIdValueObject] = None` to `CreatePipelineCommand`, then pass `command.pipelineId.value if command.pipelineId else pipeline.generate_pipeline_id()` to the entity's existing `pipelineId` argument. Apply the same exact change to component version, recipe, and recipe version using `component_version.generate_version_id()`, `recipe.generate_recipe_id()`, and `recipe_version.generate_version_id()` respectively; do not change the other constructor arguments.

- [ ] **Step 4: Verify handlers and architecture boundaries**

Run: `cd backend && uv run pytest app/packaging/domain/tests/component/test_create_component_version_command_handler.py app/packaging/domain/tests/recipe/test_create_recipe_command_handler.py app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py app/packaging/domain/tests/pipeline/test_create_pipeline_command_handler.py app/tests/test_module_import_fitness_function.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/packaging/domain/commands/component/create_component_version_command.py \
  backend/app/packaging/domain/commands/recipe/create_recipe_command.py \
  backend/app/packaging/domain/commands/recipe/create_recipe_version_command.py \
  backend/app/packaging/domain/commands/pipeline/create_pipeline_command.py \
  backend/app/packaging/domain/command_handlers/component/create_component_version_command_handler.py \
  backend/app/packaging/domain/command_handlers/recipe/create_recipe_command_handler.py \
  backend/app/packaging/domain/command_handlers/recipe/create_recipe_version_command_handler.py \
  backend/app/packaging/domain/command_handlers/pipeline/create_pipeline_command_handler.py \
  backend/app/packaging/domain/tests/component/test_create_component_version_command_handler.py \
  backend/app/packaging/domain/tests/recipe/test_create_recipe_command_handler.py \
  backend/app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py \
  backend/app/packaging/domain/tests/pipeline/test_create_pipeline_command_handler.py
git commit -m "feat(packaging): allow deterministic create identifiers"
```

### Task 4: DynamoDB Idempotency State Machine

**Files:**
- Create: `backend/app/packaging/domain/ports/idempotency_service.py`
- Create: `backend/app/packaging/adapters/services/dynamodb_idempotency_service.py`
- Create: `backend/app/packaging/adapters/tests/test_dynamodb_idempotency_service.py`
- Modify: `backend/infra/backend/packaging_app_stack.py`
- Create: `backend/infra/constructs/tests/test_backend_app_storage.py`

**Interfaces:**
- Produces: `IdempotencyScope(client_id, project_id, operation, parent_resource_id, key)`.
- Produces outcomes `ACQUIRED`, `REPLAY`, `IN_PROGRESS`, `CONFLICT`, and `RECOVER`.
- Produces: `reserve(...) -> Reservation` and `complete(...) -> None`.
- Uses `PK=IDEMPOTENCY#<clientId>#<projectId>` and `SK=<operation>#<parent-or-root>#<key>`.

- [ ] **Step 1: Define the port and add failing adapter tests**

```python
class ReservationOutcome(StrEnum):
    ACQUIRED = "ACQUIRED"
    REPLAY = "REPLAY"
    IN_PROGRESS = "IN_PROGRESS"
    CONFLICT = "CONFLICT"
    RECOVER = "RECOVER"


@dataclass(frozen=True)
class IdempotencyScope:
    client_id: str
    project_id: str
    operation: str
    parent_resource_id: str | None
    key: UUID


@dataclass(frozen=True)
class Reservation:
    outcome: ReservationOutcome
    resource_id: str
    response_status: int | None = None
    response_body: dict | None = None
```

Write moto tests for acquisition, identical replay, active lease, request-hash conflict, expired recovery, completion, and logical expiry while the item still exists physically. The expired-record test must call `reserve` twice and prove that its conditional lease takeover returns `RECOVER` to only one caller and `IN_PROGRESS` to the other.

- [ ] **Step 2: Verify the adapter test fails**

Run: `cd backend && uv run pytest app/packaging/adapters/tests/test_dynamodb_idempotency_service.py -q`

Expected: FAIL with an import error for `DynamoDBIdempotencyService`.

- [ ] **Step 3: Implement conditional reservation and completion**

```python
def reserve(self, scope, request_hash, resource_id, now):
    try:
        self._client.put_item(
            TableName=self._table_name,
            Item=self._new_item(scope, request_hash, resource_id, now),
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return Reservation(ReservationOutcome.ACQUIRED, resource_id)
    except self._client.exceptions.ConditionalCheckFailedException:
        return self._classify_existing(scope, request_hash, now)


def complete(self, scope, request_hash, resource_id, response_status, response_body, now):
    self._client.update_item(
        TableName=self._table_name,
        Key=self._key(scope),
        UpdateExpression=(
            "SET #status = :completed, responseStatus = :status, "
            "responseBody = :body, lastUpdateAt = :now, ExpireDate = :expiry"
        ),
        ConditionExpression="requestHash = :hash AND generatedResourceId = :resource",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":completed": "COMPLETED",
            ":status": response_status,
            ":body": response_body,
            ":now": int(now.timestamp()),
            ":expiry": int((now + timedelta(hours=24)).timestamp()),
            ":hash": request_hash,
            ":resource": resource_id,
        },
    )
```

Set `leaseExpiresAt` to `now + 60 seconds`. Use `ExpireDate` for table TTL and compare it in application logic before classifying an existing record. When `_classify_existing` sees an expired lease, use a conditional update against the observed lease value to take ownership; return `RECOVER` only after that update succeeds and `IN_PROGRESS` if another caller won it.

- [ ] **Step 4: Enable TTL and test the synthesized table**

```python
self._storage = backend_app_storage.BackendAppStorage(
    self,
    "PackagingAppStorage",
    app_config,
    enable_ttl=True,
)
```

Create a focused construct test that instantiates `BackendAppStorage(..., enable_ttl=True)` and asserts:

```python
{
    "TimeToLiveSpecification": {
        "AttributeName": "ExpireDate",
        "Enabled": True,
    }
}
```

- [ ] **Step 5: Verify adapter and infrastructure behavior**

Run: `cd backend && uv run pytest app/packaging/adapters/tests/test_dynamodb_idempotency_service.py infra/constructs/tests/test_backend_app_storage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/packaging/domain/ports/idempotency_service.py \
  backend/app/packaging/adapters/services/dynamodb_idempotency_service.py \
  backend/app/packaging/adapters/tests/test_dynamodb_idempotency_service.py \
  backend/infra/backend/packaging_app_stack.py \
  backend/infra/constructs/tests/test_backend_app_storage.py
git commit -m "feat(packaging): persist S2S idempotency records"
```

### Task 5: Apply Idempotency and Lifecycle Semantics to S2S Routes

**Files:**
- Create: `backend/app/packaging/entrypoints/s2s_api/idempotency.py`
- Modify: `backend/app/packaging/domain/exceptions/s2s_exception.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/problem_details.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/routers/common.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/routers/components.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/routers/component_versions.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/routers/recipes.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/routers/pipelines.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/bootstrapper.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/conftest.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_bootstrapper.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_handler.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_e2e.py`
- Modify: `backend/app/packaging/adapters/query_services/dynamodb_component_query_service.py`
- Modify: `backend/app/packaging/adapters/query_services/dynamodb_recipe_query_service.py`
- Modify: `backend/app/packaging/adapters/query_services/dynamodb_recipe_version_query_service.py`
- Modify: `backend/app/packaging/adapters/query_services/dynamodb_pipeline_query_service.py`
- Modify: `backend/app/packaging/adapters/tests/test_dynamodb_component_query_service.py`
- Modify: `backend/app/packaging/adapters/tests/test_dynamodb_recipe_query_service.py`
- Modify: `backend/app/packaging/adapters/tests/test_dynamodb_recipe_version_query_service.py`
- Modify: `backend/app/packaging/adapters/tests/test_dynamodb_pipeline_query_service.py`
- Modify: `backend/app/shared/logging/helpers.py`
- Modify: `backend/app/shared/tests/test_helpers.py`

**Interfaces:**
- Consumes: Task 3 optional IDs and Task 4 idempotency service.
- Produces: `execute_create(...) -> StoredCreateResponse`.
- Produces problems `INVALID_IDEMPOTENCY_KEY`, `IDEMPOTENCY_KEY_REUSED`, `IDEMPOTENCY_REQUEST_IN_PROGRESS`, and `RESOURCE_READ_NOT_READY`.
- Produces metrics `IdempotencyReservations`, `IdempotencyReplays`, `IdempotencyConflicts`, `IdempotencyInProgress`, and `IdempotencyRecoveries`.

- [ ] **Step 1: Add failing route tests for validation, replay, and isolation**

```python
def test_create_component_requires_idempotency_key(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event("POST", "/projects/proj-1/components", component_body,
                     scopes=["clients/packaging/component.write"]),
        lambda_context,
    )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["code"] == "INVALID_IDEMPOTENCY_KEY"
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_create_component_replays_stored_response(
    monkeypatch, mocked_dependencies, lambda_context, client_event, component_body
):
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(
        ReservationOutcome.REPLAY, "comp-fixed", 201, {"componentId": "comp-fixed"}
    )
    response = load_handler(monkeypatch, mocked_dependencies).handler(
        client_event(
            "POST", "/projects/proj-1/components", component_body,
            headers={"Idempotency-Key": "b39cdd55-774d-4bc3-81a8-70f23a03c485"},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )
    assert response["statusCode"] == 201
    assert json.loads(response["body"]) == {"componentId": "comp-fixed"}
    mocked_dependencies.command_bus.handle.assert_not_called()
```

Parameterize all five managed create endpoints. Cover malformed keys, body-hash conflict, active lease plus `Retry-After: 5`, deterministic domain failure replay, and authorization failure before reservation. Assert `POST /images` still needs no key.

- [ ] **Step 2: Verify the route tests fail**

Run: `cd backend && uv run pytest app/packaging/entrypoints/s2s_api/tests/test_handler.py -q`

Expected: FAIL because create routes do not reserve keys.

- [ ] **Step 3: Implement hashing and orchestration**

```python
@dataclass(frozen=True)
class StoredCreateResponse:
    status_code: int
    body: dict


def canonical_request_hash(request: BaseModel) -> str:
    body = request.model_dump(mode="json", by_alias=True, exclude_none=False)
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def execute_create(
    *, service, scope, request, resource_id, resource_exists,
    response_for_id, create, now,
):
    request_hash = canonical_request_hash(request)
    reservation = service.reserve(scope, request_hash, resource_id, now)
    if reservation.outcome is ReservationOutcome.REPLAY:
        return StoredCreateResponse(reservation.response_status, reservation.response_body)
    if reservation.outcome is ReservationOutcome.CONFLICT:
        raise IdempotencyKeyReused()
    if reservation.outcome is ReservationOutcome.IN_PROGRESS:
        raise IdempotencyRequestInProgress()
    if reservation.outcome is ReservationOutcome.RECOVER and resource_exists(reservation.resource_id):
        result = response_for_id(reservation.resource_id)
    else:
        result = create(reservation.resource_id)
    service.complete(
        scope, request_hash, reservation.resource_id,
        result.status_code, result.body, now,
    )
    return result
```

Catch deterministic `DomainException`, store status `422` and safe public fields, then re-raise. Replayed failures raise a typed S2S exception so the current response receives a fresh request ID. Record one Powertools `MetricUnit.Count` metric for every outcome using the interface's five metric names. Log only the SHA-256 digest of the idempotency key together with client, project, operation, generated resource ID, and outcome; never log the raw key.

- [ ] **Step 4: Wire bootstrap and all five create routes**

Construct `DynamoDBIdempotencyService` from the existing table name and low-level client and add it to `Dependencies`. Update mocked and e2e dependencies.

Authorize and validate parent references before reservation. Read and validate the `Idempotency-Key` in `common.py` after authorization and before reservation, returning `INVALID_IDEMPOTENCY_KEY` for missing or malformed UUIDs. Extend `clear_auth_headers` to mask `Idempotency-Key` case-insensitively in both `headers` and `multiValueHeaders`, with tests proving the raw UUID never reaches handler logs. Generate the ID once and inject it into the command:

```python
result = idempotency.execute_create(
    service=dependencies.idempotency_service,
    scope=common.idempotency_scope(router, client_id, project_id, "CREATE_COMPONENT"),
    request=request,
    resource_id=component_id_value_object.generate_component_id(),
    resource_exists=lambda resource_id: dependencies.component_domain_qry_srv.get_component(
        component_id_value_object.from_str(resource_id)
    ) is not None,
    response_for_id=lambda resource_id: idempotency.StoredCreateResponse(
        HTTPStatus.CREATED, {"componentId": resource_id}
    ),
    create=lambda resource_id: create_component_response(
        dependencies, project_id, client_id, request, resource_id
    ),
    now=datetime.now(timezone.utc),
)
```

Use existing model generator functions for component versions, recipes, recipe versions, and pipelines.

- [ ] **Step 5: Make reads immediate and terminal actions idempotent**

Add `ConsistentRead=True` to direct component, recipe, recipe-version, and pipeline `get_item` calls; component-version reads already use it. Preserve normal `404` behavior for unknown IDs. Map only transient adapter/read failures after a successful create or action to `ResourceReadNotReady`; do not turn an ordinary missing ID into that response.

Before archive, retirement, or release, return the normal response without dispatching when the current status is already `ARCHIVED`, `RETIRED`, or `RELEASED`:

```python
version = dependencies.component_version_qry_srv.get_component_version(component_id, version_id)
if version.status == component_version.ComponentVersionStatus.Released:
    return action_response(version_id, HTTPStatus.OK)
```

- [ ] **Step 6: Add an e2e exact-retry test and verify the slice**

```python
headers = {"Idempotency-Key": "b39cdd55-774d-4bc3-81a8-70f23a03c485"}
first = call(runtime, client_event("POST", path, body, headers=headers, scopes=scopes), context)
second = call(runtime, client_event("POST", path, body, headers=headers, scopes=scopes), context)
assert first["statusCode"] == second["statusCode"] == 201
assert json.loads(first["body"])["componentId"] == json.loads(second["body"])["componentId"]
assert len(runtime.component_query.get_components("proj-1")) == 1
```

Run: `cd backend && uv run pytest app/packaging/entrypoints/s2s_api/tests app/packaging/adapters/tests/test_dynamodb_component_query_service.py app/packaging/adapters/tests/test_dynamodb_recipe_query_service.py app/packaging/adapters/tests/test_dynamodb_recipe_version_query_service.py app/packaging/adapters/tests/test_dynamodb_pipeline_query_service.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/packaging/entrypoints/s2s_api/idempotency.py \
  backend/app/packaging/entrypoints/s2s_api/problem_details.py \
  backend/app/packaging/entrypoints/s2s_api/routers/common.py \
  backend/app/packaging/entrypoints/s2s_api/routers/components.py \
  backend/app/packaging/entrypoints/s2s_api/routers/component_versions.py \
  backend/app/packaging/entrypoints/s2s_api/routers/recipes.py \
  backend/app/packaging/entrypoints/s2s_api/routers/pipelines.py \
  backend/app/packaging/entrypoints/s2s_api/bootstrapper.py \
  backend/app/packaging/entrypoints/s2s_api/tests/conftest.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_bootstrapper.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_handler.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_e2e.py \
  backend/app/packaging/domain/exceptions/s2s_exception.py \
  backend/app/packaging/adapters/query_services/dynamodb_component_query_service.py \
  backend/app/packaging/adapters/query_services/dynamodb_recipe_query_service.py \
  backend/app/packaging/adapters/query_services/dynamodb_recipe_version_query_service.py \
  backend/app/packaging/adapters/query_services/dynamodb_pipeline_query_service.py \
  backend/app/packaging/adapters/tests/test_dynamodb_component_query_service.py \
  backend/app/packaging/adapters/tests/test_dynamodb_recipe_query_service.py \
  backend/app/packaging/adapters/tests/test_dynamodb_recipe_version_query_service.py \
  backend/app/packaging/adapters/tests/test_dynamodb_pipeline_query_service.py \
  backend/app/shared/logging/helpers.py \
  backend/app/shared/tests/test_helpers.py
git commit -m "feat(packaging): make S2S creates idempotent"
```

### Task 6: Finalize OpenAPI Lifecycle, Examples, and Verification

**Files:**
- Modify: `backend/app/packaging/entrypoints/s2s_api/schema/proserve-workbench-s2s-packaging-api-schema.yaml`
- Regenerate: `backend/app/packaging/entrypoints/s2s_api/model/api_model.py`
- Modify: `backend/app/packaging/entrypoints/s2s_api/tests/test_schema.py`
- Modify: `docs/packaging-s2s-api.md`
- Modify: `docs/bruno/VEW S2S API/Packaging/CreateComponent.yml`
- Modify: `docs/bruno/VEW S2S API/Packaging/CreateComponentVersion.yml`
- Modify: `docs/bruno/VEW S2S API/Packaging/CreateRecipe.yml`
- Modify: `docs/bruno/VEW S2S API/Packaging/CreateRecipeVersion.yml`
- Modify: `docs/bruno/VEW S2S API/Packaging/CreatePipeline.yml`
- Modify: `docs/bruno/VEW S2S API/Packaging/UpdateComponentVersion.yml`
- Modify: `docs/bruno/VEW S2S API/Packaging/UpdateRecipeVersion.yml`

**Interfaces:**
- Consumes all prior task behavior.
- Produces explicit status enums, required idempotency headers, and `Retry-After: 5` definitions.
- Produces updated narrative and executable examples for the final S2S contract.

- [ ] **Step 1: Add final failing schema assertions**

```python
def test_schema_requires_idempotency_for_managed_creates(api_schema):
    paths = api_schema["paths"]
    creates = [
        ("/projects/{projectId}/components", "post"),
        ("/projects/{projectId}/components/{componentId}/versions", "post"),
        ("/projects/{projectId}/recipes", "post"),
        ("/projects/{projectId}/recipes/{recipeId}/versions", "post"),
        ("/projects/{projectId}/pipelines", "post"),
    ]
    reference = {"$ref": "#/components/parameters/IdempotencyKey"}
    for path, method in creates:
        assert reference in paths[path][method]["parameters"]
        assert paths[path][method]["x-amazon-apigateway-request-validator"] == "body-only"
    assert reference not in paths["/projects/{projectId}/images"]["post"]["parameters"]


def test_schema_enumerates_resource_statuses(api_schema):
    schemas = api_schema["components"]["schemas"]
    assert schemas["ComponentVersionStatus"]["enum"] == [
        "CREATING", "CREATED", "TESTING", "VALIDATED",
        "UPDATING", "RELEASED", "RETIRED", "FAILED",
    ]
    assert schemas["PipelineStatus"]["enum"] == [
        "CREATING", "CREATED", "UPDATING", "RETIRED", "FAILED",
    ]
```

Add assertions that every component-version, recipe-version, and pipeline `202` response declares `Retry-After` with default `5`, while synchronous releases do not.

- [ ] **Step 2: Verify the schema tests fail**

Run: `cd backend && uv run pytest app/packaging/entrypoints/s2s_api/tests/test_schema.py -q`

Expected: FAIL on missing headers or unrestricted status strings.

- [ ] **Step 3: Complete OpenAPI and regenerate models**

```yaml
IdempotencyKey:
  name: Idempotency-Key
  in: header
  required: true
  schema:
    type: string
    format: uuid
```

Define `ComponentStatus`, `ComponentVersionStatus`, `RecipeStatus`, `RecipeVersionStatus`, `PipelineStatus`, and `ImageStatus`; reference them from representations. Set all asynchronous action response header defaults to `'5'`. Regenerate and format `api_model.py` with the Task 1 command.

Add a `body-only` API Gateway request validator and assign it to the five managed create operations:

```yaml
x-amazon-apigateway-request-validators:
  body-only:
    validateRequestBody: true
    validateRequestParameters: false
```

Keep `Idempotency-Key` required in the OpenAPI operation, but leave its runtime validation to Lambda so missing and malformed keys receive the stable `INVALID_IDEMPOTENCY_KEY` problem instead of API Gateway's generic parameter-validation response. Do not change validators on any other operation.

- [ ] **Step 4: Update narrative and Bruno examples**

For each managed create request add:

```yaml
headers:
  - name: Idempotency-Key
    value: 7b60fdad-c26b-4189-abf8-30c7bb901e72
```

Replace component YAML strings with `componentVersionDefinition`, replace recipe request `recipeComponentsVersions` with `configuredComponentsVersions`, document both recipe read views, and retain the warning that image-build POST is not idempotent and is outside Terraform scope.

- [ ] **Step 5: Run focused verification**

```bash
cd backend
uv run pytest \
  app/packaging/domain/tests/component/test_create_component_version_command_handler.py \
  app/packaging/domain/tests/recipe/test_create_recipe_command_handler.py \
  app/packaging/domain/tests/recipe/test_create_recipe_version_command_handler.py \
  app/packaging/domain/tests/recipe/test_update_recipe_version_command_handler.py \
  app/packaging/domain/tests/pipeline/test_create_pipeline_command_handler.py \
  app/packaging/adapters/tests/test_dynamodb_idempotency_service.py \
  app/packaging/entrypoints/s2s_api/tests \
  infra/constructs/tests/test_backend_app_storage.py -q
```

Expected: PASS.

- [ ] **Step 6: Run full backend verification**

```bash
cd backend
uv run pytest app -n auto -q
cd ..
SKIP=frontend-web-lint,frontend-infra-lint uvx pre-commit run --all-files --show-diff-on-failure
```

Expected: all tests and hooks pass.

- [ ] **Step 7: Inspect scope and commit**

Run:

```bash
git diff --check
git diff --name-only
git status --short
```

Confirm the task introduced no provider project, no image-create idempotency, no user API schema change, and no unrelated files. Then commit:

```bash
git add backend/app/packaging/entrypoints/s2s_api/schema/proserve-workbench-s2s-packaging-api-schema.yaml \
  backend/app/packaging/entrypoints/s2s_api/model/api_model.py \
  backend/app/packaging/entrypoints/s2s_api/tests/test_schema.py \
  docs/packaging-s2s-api.md \
  'docs/bruno/VEW S2S API/Packaging/CreateComponent.yml' \
  'docs/bruno/VEW S2S API/Packaging/CreateComponentVersion.yml' \
  'docs/bruno/VEW S2S API/Packaging/CreateRecipe.yml' \
  'docs/bruno/VEW S2S API/Packaging/CreateRecipeVersion.yml' \
  'docs/bruno/VEW S2S API/Packaging/CreatePipeline.yml' \
  'docs/bruno/VEW S2S API/Packaging/UpdateComponentVersion.yml' \
  'docs/bruno/VEW S2S API/Packaging/UpdateRecipeVersion.yml'
git commit -m "docs(packaging): publish Terraform-ready S2S contract"
```
