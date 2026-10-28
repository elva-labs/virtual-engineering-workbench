import importlib
import json
from functools import partial
from types import SimpleNamespace
from unittest import mock

import boto3
import moto
import pytest
from freezegun import freeze_time

from app.packaging.adapters.query_services.dynamodb_component_query_service import DynamoDBComponentQueryService
from app.packaging.adapters.query_services.dynamodb_component_version_query_service import (
    DynamoDBComponentVersionQueryService,
)
from app.packaging.adapters.query_services.dynamodb_pipeline_query_service import DynamoDBPipelineQueryService
from app.packaging.adapters.query_services.dynamodb_recipe_query_service import DynamoDBRecipeQueryService
from app.packaging.adapters.query_services.dynamodb_recipe_version_query_service import (
    DynamoDBRecipeVersionQueryService,
)
from app.packaging.adapters.repository import dynamo_entity_config
from app.packaging.adapters.services.dynamodb_idempotency_service import DynamoDBIdempotencyService
from app.packaging.domain.command_handlers.component import (
    create_component_command_handler,
    create_component_version_command_handler,
)
from app.packaging.domain.command_handlers.pipeline import create_pipeline_command_handler
from app.packaging.domain.command_handlers.recipe import (
    create_recipe_command_handler,
    create_recipe_version_command_handler,
)
from app.packaging.domain.commands.component import create_component_command, create_component_version_command
from app.packaging.domain.commands.pipeline import create_pipeline_command
from app.packaging.domain.commands.recipe import create_recipe_command, create_recipe_version_command
from app.packaging.domain.model.recipe.recipe_version import RecipeVersion, RecipeVersionPrimaryKey
from app.packaging.domain.ports.service_client_project_access_service import ServiceClientProjectAccessService
from app.packaging.domain.query_services.component_domain_query_service import ComponentDomainQueryService
from app.packaging.domain.query_services.component_version_domain_query_service import (
    ComponentVersionDomainQueryService,
)
from app.packaging.domain.query_services.image_domain_query_service import ImageDomainQueryService
from app.packaging.domain.query_services.pipeline_domain_query_service import PipelineDomainQueryService
from app.packaging.domain.query_services.recipe_domain_query_service import RecipeDomainQueryService
from app.packaging.domain.query_services.recipe_version_domain_query_service import RecipeVersionDomainQueryService
from app.packaging.entrypoints.s2s_api import bootstrapper
from app.shared.adapters.message_bus import in_memory_command_bus
from app.shared.adapters.message_bus.message_bus import MessageBus
from app.shared.adapters.unit_of_work_v2 import dynamodb_unit_of_work

PACKAGING_TABLE = "packaging-s2s-e2e"
ENTITIES_GSI = "entities"
INVERTED_GSI = "inverted"
STATUS_GSI = "status"


class ProjectAccess(ServiceClientProjectAccessService):
    def require_access(self, client_id: str, project_id: str) -> None:
        return None


def create_table(dynamodb):
    return dynamodb.create_table(
        TableName=PACKAGING_TABLE,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI_PK", "AttributeType": "S"},
            {"AttributeName": "GSI_SK", "AttributeType": "S"},
            {"AttributeName": "entity", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": STATUS_GSI,
                "KeySchema": [
                    {"AttributeName": "GSI_PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI_SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": ENTITIES_GSI,
                "KeySchema": [
                    {"AttributeName": "entity", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": INVERTED_GSI,
                "KeySchema": [{"AttributeName": "SK", "KeyType": "HASH"}, {"AttributeName": "PK", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
        ],
    )


def load_handler(monkeypatch, dependencies):
    monkeypatch.setattr(bootstrapper, "bootstrap", mock.Mock(return_value=dependencies))
    from app.packaging.entrypoints.s2s_api import handler

    return importlib.reload(handler)


@pytest.fixture()
def e2e_runtime(monkeypatch):
    with moto.mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
        create_table(dynamodb)
        client = dynamodb.meta.client
        uow = dynamodb_unit_of_work.DynamoDBUnitOfWork(
            table_name=PACKAGING_TABLE,
            dynamodb_client=client,
            repo_factories=dynamo_entity_config.EntityConfigurator(table_name=PACKAGING_TABLE).repo_factories(),
            logger=mock.Mock(),
        )
        component_query = DynamoDBComponentQueryService(PACKAGING_TABLE, client, INVERTED_GSI, ENTITIES_GSI)
        version_query = DynamoDBComponentVersionQueryService(PACKAGING_TABLE, client, STATUS_GSI, ENTITIES_GSI)
        recipe_query = DynamoDBRecipeQueryService(PACKAGING_TABLE, client, ENTITIES_GSI)
        recipe_version_query = DynamoDBRecipeVersionQueryService(PACKAGING_TABLE, client, ENTITIES_GSI, STATUS_GSI)
        pipeline_query = DynamoDBPipelineQueryService(PACKAGING_TABLE, client, INVERTED_GSI, ENTITIES_GSI)
        message_bus = mock.create_autospec(MessageBus)
        command_bus = in_memory_command_bus.InMemoryCommandBus(logger=mock.Mock())
        command_bus.register_handler(
            create_component_command.CreateComponentCommand,
            lambda command: create_component_command_handler.handle(command, uow),
        ).register_handler(
            create_component_version_command.CreateComponentVersionCommand,
            lambda command: create_component_version_command_handler.handle(
                command, uow, message_bus, component_query, version_query
            ),
        ).register_handler(
            create_recipe_command.CreateRecipeCommand,
            lambda command: create_recipe_command_handler.handle(command, uow),
        ).register_handler(
            create_recipe_version_command.CreateRecipeVersionCommand,
            lambda command: create_recipe_version_command_handler.handle(
                command=command,
                uow=uow,
                message_bus=message_bus,
                component_version_qry_srv=version_query,
                recipe_version_qry_srv=recipe_version_query,
                recipe_qry_srv=recipe_query,
                parameter_srv=mock.Mock(get_parameter_value=lambda name: "ami-0123456789abcdef0"),
                mandatory_components_list_qry_srv=mock.Mock(get_mandatory_components_list=lambda **kwargs: None),
                system_configuration_mapping={"Linux": {"amd64": {"Ubuntu 24": {"ami_ssm_param_name": "ami"}}}},
                component_qry_srv=component_query,
            ),
        ).register_handler(
            create_pipeline_command.CreatePipelineCommand,
            lambda command: create_pipeline_command_handler.handle(
                command=command,
                uow=uow,
                message_bus=message_bus,
                recipe_qry_srv=recipe_query,
                recipe_version_qry_srv=recipe_version_query,
                pipeline_srv=mock.Mock(get_pipeline_allowed_build_instance_types=lambda **kwargs: ["t3.large"]),
            ),
        )
        dependencies = bootstrapper.Dependencies(
            project_access_service=ProjectAccess(),
            command_bus=command_bus,
            component_domain_qry_srv=ComponentDomainQueryService(component_query),
            component_version_domain_qry_srv=ComponentVersionDomainQueryService(
                component_query, version_query, mock.Mock()
            ),
            component_version_qry_srv=version_query,
            recipe_domain_qry_srv=RecipeDomainQueryService(recipe_query),
            recipe_version_domain_qry_srv=RecipeVersionDomainQueryService(recipe_query, recipe_version_query),
            pipeline_domain_qry_srv=PipelineDomainQueryService(pipeline_query),
            image_domain_qry_srv=mock.create_autospec(ImageDomainQueryService, instance=True),
            idempotency_service=DynamoDBIdempotencyService(PACKAGING_TABLE, client),
            resume_component_version_creation=lambda component_id, version_id, definition: (
                create_component_version_command_handler.resume_creation(
                    component_id, version_id, definition, version_query, message_bus
                )
            ),
            resume_recipe_version_creation=lambda project_id, recipe_id, version_id: (
                create_recipe_version_command_handler.resume_creation(
                    project_id, recipe_id, version_id, recipe_version_query, message_bus
                )
            ),
            resume_pipeline_creation=lambda project_id, pipeline_id: create_pipeline_command_handler.resume_creation(
                project_id, pipeline_id, pipeline_query, message_bus
            ),
        )
        yield SimpleNamespace(
            handler=load_handler(monkeypatch, dependencies),
            component_query=component_query,
            version_query=version_query,
            recipe_query=recipe_query,
            recipe_version_query=recipe_version_query,
            pipeline_query=pipeline_query,
            message_bus=message_bus,
            uow=uow,
            table=dynamodb.Table(PACKAGING_TABLE),
        )


def call(runtime, event, context):
    return runtime.handler.handler(event, context)


def test_component_create_read_and_version_command_round_trip(
    e2e_runtime, client_event, lambda_context, component_body, version_body
):
    created = call(
        e2e_runtime,
        client_event(
            "POST",
            "/projects/proj-1/components",
            component_body,
            headers={"Idempotency-Key": "b39cdd55-774d-4bc3-81a8-70f23a03c485"},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )
    component_id = json.loads(created["body"])["componentId"]
    read = call(
        e2e_runtime,
        client_event("GET", f"/projects/proj-1/components/{component_id}", scopes=["clients/packaging/component.read"]),
        lambda_context,
    )
    version = call(
        e2e_runtime,
        client_event(
            "POST",
            f"/projects/proj-1/components/{component_id}/versions",
            version_body,
            headers={"Idempotency-Key": "2676b69a-e94f-4c2a-8d39-aed5fb209546"},
            scopes=["clients/packaging/component.write"],
        ),
        lambda_context,
    )

    assert created["statusCode"] == 201
    assert json.loads(read["body"])["component"]["componentId"] == component_id
    assert version["statusCode"] == 202
    assert json.loads(version["body"])["componentVersionId"].startswith("vers-")


def test_recipe_create_and_read_round_trip_uses_internal_id(e2e_runtime, client_event, lambda_context, recipe_body):
    created = call(
        e2e_runtime,
        client_event(
            "POST",
            "/projects/proj-1/recipes",
            recipe_body,
            headers={"Idempotency-Key": "b39cdd55-774d-4bc3-81a8-70f23a03c485"},
            scopes=["clients/packaging/recipe.write"],
        ),
        lambda_context,
    )
    recipe_id = json.loads(created["body"])["recipeId"]
    read = call(
        e2e_runtime,
        client_event("GET", f"/projects/proj-1/recipes/{recipe_id}", scopes=["clients/packaging/recipe.read"]),
        lambda_context,
    )

    assert created["statusCode"] == 201
    assert recipe_id.startswith("reci-")
    assert json.loads(read["body"])["recipe"]["recipeId"] == recipe_id


def test_component_create_exact_retry_returns_same_id_and_creates_one_resource(
    e2e_runtime, client_event, lambda_context, component_body
):
    headers = {"Idempotency-Key": "b39cdd55-774d-4bc3-81a8-70f23a03c485"}
    event = client_event(
        "POST",
        "/projects/proj-1/components",
        component_body,
        headers=headers,
        scopes=["clients/packaging/component.write"],
    )

    first = call(e2e_runtime, event, lambda_context)
    second = call(e2e_runtime, event, lambda_context)

    assert first["statusCode"] == second["statusCode"] == 201
    assert json.loads(first["body"])["componentId"] == json.loads(second["body"])["componentId"]
    assert len(e2e_runtime.component_query.get_components("proj-1")) == 1


@pytest.mark.parametrize("resource", ["component-version", "recipe-version", "pipeline"])
@pytest.mark.parametrize("already_started", [False, True], ids=["missing-publication", "retired"])
def test_async_create_recovers_committed_resource_only_after_workflow_publication(
    e2e_runtime,
    client_event,
    lambda_context,
    component_body,
    version_body,
    recipe_body,
    recipe_version_body,
    pipeline_body,
    resource,
    already_started,
):
    runtime = e2e_runtime
    parent_kind = "components" if resource == "component-version" else "recipes"
    parent_field = "componentId" if parent_kind == "components" else "recipeId"
    scopes = ["clients/packaging/component.write", "clients/packaging/recipe.write", "clients/packaging/pipeline.write"]
    created_parent = call(
        runtime,
        client_event(
            "POST",
            f"/projects/proj-1/{parent_kind}",
            component_body if parent_kind == "components" else recipe_body,
            headers={"Idempotency-Key": "667dd076-a951-4c91-934f-649344b224f9"},
            scopes=scopes,
        ),
        lambda_context,
    )
    assert created_parent["statusCode"] == 201
    parent_id = json.loads(created_parent["body"])[parent_field]
    if resource == "pipeline":
        with runtime.uow:
            runtime.uow.get_repository(RecipeVersionPrimaryKey, RecipeVersion).add(
                RecipeVersion(
                    recipeId=parent_id,
                    recipeVersionId="vers-source",
                    parentImageUpstreamId="ami-1",
                    recipeComponentsVersions=[],
                    recipeName="Source",
                    recipeVersionDescription="Source",
                    recipeVersionName="1.0.0",
                    recipeVersionVolumeSize="8",
                    status="RELEASED",
                    createDate="2026-09-18",
                    createdBy="actor",
                    lastUpdateDate="2026-09-18",
                    lastUpdatedBy="actor",
                )
            )
            runtime.uow.commit()
        pipeline_body.update(recipeId=parent_id, recipeVersionId="vers-source")
        body, field, path = pipeline_body, "pipelineId", "/projects/proj-1/pipelines"
        query = partial(runtime.pipeline_query.get_pipeline, "proj-1")
    else:
        body = version_body if resource == "component-version" else recipe_version_body
        field = "componentVersionId" if resource == "component-version" else "recipeVersionId"
        path = f"/projects/proj-1/{parent_kind}/{parent_id}/versions"
        query_service = runtime.version_query if resource == "component-version" else runtime.recipe_version_query
        query = partial(
            (
                query_service.get_component_version
                if resource == "component-version"
                else query_service.get_recipe_version
            ),
            parent_id,
        )
    request_key = "b39cdd55-774d-4bc3-81a8-70f23a03c485"
    event = client_event("POST", path, body, headers={"Idempotency-Key": request_key}, scopes=scopes)
    runtime.message_bus.publish.side_effect = [
        RuntimeError("publish unavailable"),
        RuntimeError("still unavailable"),
        None,
    ]

    def reservation():
        return next(
            item
            for item in runtime.table.scan(ConsistentRead=True)["Items"]
            if item["PK"].startswith("IDEMPOTENCY#") and item["SK"].endswith(request_key)
        )

    with freeze_time("2026-09-18T12:00:00Z") as clock:
        first = call(runtime, event, lambda_context)
        assert first["statusCode"] == 500
        record = reservation()
        resource_id = record["generatedResourceId"]
        assert record["status"] == "IN_PROGRESS"
        persisted = query(resource_id)
        assert persisted.status == "CREATING"
        clock.tick(61)
        if already_started:
            item = next(item for item in runtime.table.scan()["Items"] if item.get(field) == resource_id)
            runtime.table.update_item(
                Key={"PK": item["PK"], "SK": item["SK"]},
                UpdateExpression="SET #status = :retired",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":retired": "RETIRED"},
            )
            recovered = call(runtime, event, lambda_context)
            assert recovered["statusCode"] == 202
            assert json.loads(recovered["body"]) == {field: resource_id}
            assert reservation()["status"] == "COMPLETED"
            assert query(resource_id).status == "RETIRED"
            assert runtime.message_bus.publish.call_count == 1
            return
        failed_resume = call(runtime, event, lambda_context)
        assert failed_resume["statusCode"] == 500
        assert json.loads(failed_resume["body"])["retryable"] is True
        assert reservation()["status"] == "IN_PROGRESS"
        clock.tick(61)
        recovered = call(runtime, event, lambda_context)
        assert recovered["statusCode"] == 202
        assert json.loads(recovered["body"]) == {field: resource_id}
        assert reservation()["status"] == "COMPLETED"
        assert query(resource_id) == persisted
        assert runtime.message_bus.publish.call_count == 3
        events = [
            call.args[0].model_dump(exclude={"event_context"}) for call in runtime.message_bus.publish.call_args_list
        ]
        assert events[0] == events[1] == events[2]
        replay = call(runtime, event, lambda_context)
        assert replay["body"] == recovered["body"]
        assert runtime.message_bus.publish.call_count == 3
