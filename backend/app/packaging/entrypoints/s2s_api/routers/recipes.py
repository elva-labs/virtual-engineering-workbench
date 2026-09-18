from datetime import datetime, timezone
from http import HTTPStatus

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types

from app.packaging.domain.commands.recipe import (
    archive_recipe_command,
    create_recipe_command,
    create_recipe_version_command,
    release_recipe_version_command,
    retire_recipe_version_command,
    update_recipe_version_command,
)
from app.packaging.domain.model.recipe import recipe, recipe_version
from app.packaging.domain.value_objects.recipe import (
    recipe_description_value_object,
    recipe_id_value_object,
    recipe_name_value_object,
    recipe_system_configuration_value_object,
)
from app.packaging.domain.value_objects.recipe_version import (
    recipe_version_components_versions_value_object,
    recipe_version_description_value_object,
    recipe_version_id_value_object,
    recipe_version_integration_value_object,
    recipe_version_release_type_value_object,
    recipe_version_volume_size_value_object,
)
from app.packaging.domain.value_objects.shared import project_id_value_object, user_id_value_object
from app.packaging.entrypoints.s2s_api import bootstrapper, idempotency
from app.packaging.entrypoints.s2s_api.model import api_model
from app.packaging.entrypoints.s2s_api.routers import common

tracer = Tracer()

READ_SCOPE = "clients/packaging/recipe.read"
WRITE_SCOPE = "clients/packaging/recipe.write"
RELEASE_SCOPE = "clients/packaging/recipe.release"


def recipe_version_model(version) -> api_model.RecipeVersion:
    payload = version.model_dump()
    payload["effectiveComponentsVersions"] = payload.pop("recipeComponentsVersions")
    configured = payload.pop("configuredRecipeComponentsVersions", None)
    if configured is not None:
        payload["configuredComponentsVersions"] = configured
    return api_model.RecipeVersion.model_validate(payload)


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()

    def authorize(project_id: str, scope: str) -> str:
        current_client_id = common.client_id(router)
        dependencies.project_access_service.require_access(current_client_id, project_id)
        common.require_scope(router, scope)
        return current_client_id

    def require_recipe(project_id: str, recipe_id: str) -> None:
        dependencies.recipe_domain_qry_srv.require_recipe_in_project(
            project_id_value_object.from_str(project_id),
            recipe_id_value_object.from_str(recipe_id),
        )

    def action_response(recipe_version_id: str) -> api_gateway.Response:
        return api_gateway.Response(
            status_code=HTTPStatus.ACCEPTED,
            body=api_model.RecipeVersionActionResponse(recipeVersionId=recipe_version_id),
            headers={**common.NO_STORE, "Retry-After": "5"},
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method
    @router.post("/projects/<project_id>/recipes")
    def create_recipe(project_id: str, request: api_model.CreateRecipeRequest):
        client_id = authorize(project_id, WRITE_SCOPE)
        scope = common.idempotency_scope(router, client_id, project_id, "CREATE_RECIPE")
        result = idempotency.execute_create(
            service=dependencies.idempotency_service,
            scope=scope,
            request=request,
            resource_id=recipe.generate_recipe_id(),
            resource_exists=lambda resource_id: dependencies.recipe_domain_qry_srv.get_recipe(
                project_id_value_object.from_str(project_id),
                recipe_id_value_object.from_str(resource_id),
            )
            is not None,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(
                HTTPStatus.CREATED, {"recipeId": resource_id}
            ),
            create=lambda resource_id: create_recipe_response(
                dependencies, project_id, client_id, request, resource_id
            ),
            now=datetime.now(timezone.utc),
        )
        return api_gateway.Response(
            status_code=result.status_code,
            body=result.body,
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method
    @router.get("/projects/<project_id>/recipes")
    def list_recipes(project_id: str):
        authorize(project_id, READ_SCOPE)
        recipes = dependencies.recipe_domain_qry_srv.get_recipes(project_id_value_object.from_str(project_id))
        return api_model.RecipePage(
            recipes=[api_model.Recipe.model_validate(recipe.model_dump()) for recipe in recipes]
        )

    @tracer.capture_method
    @router.get("/projects/<project_id>/recipes/<recipe_id>")
    def get_recipe(project_id: str, recipe_id: str):
        authorize(project_id, READ_SCOPE)
        require_recipe(project_id, recipe_id)
        recipe = dependencies.recipe_domain_qry_srv.get_recipe(
            project_id_value_object.from_str(project_id),
            recipe_id_value_object.from_str(recipe_id),
        )
        return api_model.RecipeResponse(recipe=api_model.Recipe.model_validate(recipe.model_dump()))

    @tracer.capture_method
    @router.delete("/projects/<project_id>/recipes/<recipe_id>")
    def archive_recipe(project_id: str, recipe_id: str):
        client_id = authorize(project_id, WRITE_SCOPE)
        require_recipe(project_id, recipe_id)
        existing = dependencies.recipe_domain_qry_srv.get_recipe(
            project_id_value_object.from_str(project_id),
            recipe_id_value_object.from_str(recipe_id),
        )
        if existing.status == recipe.RecipeStatus.Archived:
            return api_gateway.Response(status_code=HTTPStatus.OK, body={}, headers=common.NO_STORE)
        dependencies.command_bus.handle(
            archive_recipe_command.ArchiveRecipeCommand(
                projectId=project_id_value_object.from_str(project_id),
                recipeId=recipe_id_value_object.from_str(recipe_id),
                lastUpdatedBy=user_id_value_object.from_str(f"service:{client_id}"),
            )
        )
        return api_gateway.Response(status_code=HTTPStatus.OK, body={}, headers=common.NO_STORE)

    @tracer.capture_method
    @router.post("/projects/<project_id>/recipes/<recipe_id>/versions")
    def create_recipe_version(project_id: str, recipe_id: str, request: api_model.CreateRecipeVersionRequest):
        client_id = authorize(project_id, WRITE_SCOPE)
        require_recipe(project_id, recipe_id)
        scope = common.idempotency_scope(
            router,
            client_id,
            project_id,
            "CREATE_RECIPE_VERSION",
            recipe_id,
        )
        result = idempotency.execute_create(
            service=dependencies.idempotency_service,
            scope=scope,
            request=request,
            resource_id=recipe_version.generate_version_id(),
            resource_exists=lambda resource_id: dependencies.recipe_version_domain_qry_srv.get_recipe_version(
                recipe_id_value_object.from_str(recipe_id),
                recipe_version_id_value_object.from_str(resource_id),
            )
            is not None,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(
                HTTPStatus.ACCEPTED, {"recipeVersionId": resource_id}
            ),
            create=lambda resource_id: create_recipe_version_response(
                dependencies, project_id, recipe_id, client_id, request, resource_id
            ),
            now=datetime.now(timezone.utc),
        )
        return api_gateway.Response(
            status_code=result.status_code,
            body=result.body,
            headers={**common.NO_STORE, "Retry-After": "5"},
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method
    @router.get("/projects/<project_id>/recipes/<recipe_id>/versions")
    def list_recipe_versions(project_id: str, recipe_id: str):
        authorize(project_id, READ_SCOPE)
        require_recipe(project_id, recipe_id)
        versions = dependencies.recipe_version_domain_qry_srv.get_recipe_versions(
            recipe_id_value_object.from_str(recipe_id)
        )
        return api_model.RecipeVersionPage(recipe_versions=[recipe_version_model(version) for version in versions])

    @tracer.capture_method
    @router.get("/projects/<project_id>/recipes/<recipe_id>/versions/<version_id>")
    def get_recipe_version(project_id: str, recipe_id: str, version_id: str):
        authorize(project_id, READ_SCOPE)
        require_recipe(project_id, recipe_id)
        dependencies.recipe_version_domain_qry_srv.require_recipe_version_in_recipe(
            recipe_id_value_object.from_str(recipe_id),
            recipe_version_id_value_object.from_str(version_id),
        )
        version = dependencies.recipe_version_domain_qry_srv.get_recipe_version(
            recipe_id_value_object.from_str(recipe_id),
            recipe_version_id_value_object.from_str(version_id),
        )
        return api_model.RecipeVersionResponse(recipe_version=recipe_version_model(version))

    @tracer.capture_method
    @router.put("/projects/<project_id>/recipes/<recipe_id>/versions/<version_id>")
    def update_recipe_version(
        project_id: str,
        recipe_id: str,
        version_id: str,
        request: api_model.UpdateRecipeVersionRequest,
    ):
        client_id = authorize(project_id, WRITE_SCOPE)
        require_recipe(project_id, recipe_id)
        dependencies.recipe_version_domain_qry_srv.require_recipe_version_in_recipe(
            recipe_id_value_object.from_str(recipe_id),
            recipe_version_id_value_object.from_str(version_id),
        )
        dependencies.command_bus.handle(
            update_recipe_version_command.UpdateRecipeVersionCommand(
                projectId=project_id_value_object.from_str(project_id),
                recipeId=recipe_id_value_object.from_str(recipe_id),
                recipeVersionId=recipe_version_id_value_object.from_str(version_id),
                recipeComponentsVersions=recipe_version_components_versions_value_object.from_list(
                    request.configuredComponentsVersions
                ),
                recipeVersionDescription=recipe_version_description_value_object.from_str(
                    request.recipeVersionDescription
                ),
                recipeVersionVolumeSize=recipe_version_volume_size_value_object.from_str(
                    request.recipeVersionVolumeSize
                ),
                recipeVersionIntegrations=recipe_version_integration_value_object.from_str_array(
                    request.recipeVersionIntegrations or []
                ),
                lastUpdatedBy=user_id_value_object.from_str(f"service:{client_id}"),
            )
        )
        return action_response(version_id)

    @tracer.capture_method
    @router.post("/projects/<project_id>/recipes/<recipe_id>/versions/<version_id>/release")
    def release_recipe_version(project_id: str, recipe_id: str, version_id: str):
        client_id = authorize(project_id, RELEASE_SCOPE)
        require_recipe(project_id, recipe_id)
        dependencies.recipe_version_domain_qry_srv.require_recipe_version_in_recipe(
            recipe_id_value_object.from_str(recipe_id),
            recipe_version_id_value_object.from_str(version_id),
        )
        version = dependencies.recipe_version_domain_qry_srv.get_recipe_version(
            recipe_id_value_object.from_str(recipe_id),
            recipe_version_id_value_object.from_str(version_id),
        )
        if version.status == recipe_version.RecipeVersionStatus.Released:
            return api_gateway.Response(
                status_code=HTTPStatus.OK,
                body=api_model.RecipeVersionActionResponse(recipeVersionId=version_id),
                headers=common.NO_STORE,
                content_type=content_types.APPLICATION_JSON,
            )
        result = dependencies.command_bus.handle(
            release_recipe_version_command.ReleaseRecipeVersionCommand(
                recipeId=recipe_id_value_object.from_str(recipe_id),
                recipeVersionId=recipe_version_id_value_object.from_str(version_id),
                lastUpdatedBy=user_id_value_object.from_str(f"service:{client_id}"),
            )
        )
        return api_gateway.Response(
            status_code=HTTPStatus.OK,
            body=api_model.RecipeVersionActionResponse(recipeVersionId=result["recipeVersionId"]),
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method
    @router.delete("/projects/<project_id>/recipes/<recipe_id>/versions/<version_id>")
    def retire_recipe_version(project_id: str, recipe_id: str, version_id: str):
        client_id = authorize(project_id, WRITE_SCOPE)
        require_recipe(project_id, recipe_id)
        dependencies.recipe_version_domain_qry_srv.require_recipe_version_in_recipe(
            recipe_id_value_object.from_str(recipe_id),
            recipe_version_id_value_object.from_str(version_id),
        )
        version = dependencies.recipe_version_domain_qry_srv.get_recipe_version(
            recipe_id_value_object.from_str(recipe_id),
            recipe_version_id_value_object.from_str(version_id),
        )
        if version.status == recipe_version.RecipeVersionStatus.Retired:
            return action_response(version_id)
        dependencies.command_bus.handle(
            retire_recipe_version_command.RetireRecipeVersionCommand(
                projectId=project_id_value_object.from_str(project_id),
                recipeId=recipe_id_value_object.from_str(recipe_id),
                recipeVersionId=recipe_version_id_value_object.from_str(version_id),
                userRoles=[],
                serviceAuthorized=True,
                lastUpdatedBy=user_id_value_object.from_str(f"service:{client_id}"),
            )
        )
        return action_response(version_id)

    return router


def create_recipe_response(
    dependencies: bootstrapper.Dependencies,
    project_id: str,
    client_id: str,
    request: api_model.CreateRecipeRequest,
    recipe_id: str,
) -> idempotency.StoredCreateResponse:
    dependencies.command_bus.handle(
        create_recipe_command.CreateRecipeCommand(
            projectId=project_id_value_object.from_str(project_id),
            recipeId=recipe_id_value_object.from_str(recipe_id),
            recipeName=recipe_name_value_object.from_str(request.recipeName),
            recipeDescription=recipe_description_value_object.from_str(request.recipeDescription),
            recipeSystemConfiguration=recipe_system_configuration_value_object.from_attrs(
                platform=request.recipePlatform,
                architecture=request.recipeArchitecture,
                os_version=request.recipeOsVersion,
            ),
            createdBy=user_id_value_object.from_str(f"service:{client_id}"),
        )
    )
    return idempotency.StoredCreateResponse(HTTPStatus.CREATED, {"recipeId": recipe_id})


def create_recipe_version_response(
    dependencies: bootstrapper.Dependencies,
    project_id: str,
    recipe_id: str,
    client_id: str,
    request: api_model.CreateRecipeVersionRequest,
    version_id: str,
) -> idempotency.StoredCreateResponse:
    dependencies.command_bus.handle(
        create_recipe_version_command.CreateRecipeVersionCommand(
            projectId=project_id_value_object.from_str(project_id),
            recipeId=recipe_id_value_object.from_str(recipe_id),
            recipeVersionId=recipe_version_id_value_object.from_str(version_id),
            recipeComponentsVersions=recipe_version_components_versions_value_object.from_list(
                request.configuredComponentsVersions
            ),
            recipeVersionDescription=recipe_version_description_value_object.from_str(request.recipeVersionDescription),
            recipeVersionReleaseType=recipe_version_release_type_value_object.from_str(
                request.recipeVersionReleaseType.value
            ),
            recipeVersionVolumeSize=recipe_version_volume_size_value_object.from_str(request.recipeVersionVolumeSize),
            recipeVersionIntegrations=recipe_version_integration_value_object.from_str_array(
                request.recipeVersionIntegrations or []
            ),
            createdBy=user_id_value_object.from_str(f"service:{client_id}"),
        )
    )
    return idempotency.StoredCreateResponse(HTTPStatus.ACCEPTED, {"recipeVersionId": version_id})
