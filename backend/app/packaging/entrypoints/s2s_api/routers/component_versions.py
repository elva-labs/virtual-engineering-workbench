from datetime import datetime, timezone
from http import HTTPStatus

import yaml
from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types
from aws_lambda_powertools.metrics import MetricUnit
from pydantic import ValidationError

from app.packaging.domain.commands.component import (
    create_component_version_command,
    release_component_version_command,
    retire_component_version_command,
    update_component_version_command,
)
from app.packaging.domain.exceptions.domain_exception import DomainException
from app.packaging.domain.exceptions.s2s_exception import InvalidComponentDefinition, StoredDefinitionInvalid
from app.packaging.domain.model.component import component_version
from app.packaging.domain.value_objects.component import component_id_value_object
from app.packaging.domain.value_objects.component_version import (
    component_license_dashboard_url_value_object,
    component_software_vendor_value_object,
    component_software_version_notes_value_object,
    component_software_version_value_object,
    component_version_dependencies_value_object,
    component_version_description_value_object,
    component_version_id_value_object,
    component_version_release_type_value_object,
    component_version_yaml_definition_value_object,
)
from app.packaging.domain.value_objects.shared import project_id_value_object, user_id_value_object
from app.packaging.entrypoints.s2s_api import bootstrapper, idempotency
from app.packaging.entrypoints.s2s_api.model import api_model
from app.packaging.entrypoints.s2s_api.routers import common

tracer = Tracer()

READ_SCOPE = "clients/packaging/component.read"
WRITE_SCOPE = "clients/packaging/component.write"
RELEASE_SCOPE = "clients/packaging/component.release"


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()

    def authorize(project_id: str, scope: str) -> str:
        current_client_id = common.client_id(router)
        dependencies.project_access_service.require_access(current_client_id, project_id)
        common.require_scope(router, scope)
        return current_client_id

    def require_component(project_id: str, component_id: str) -> None:
        dependencies.component_domain_qry_srv.require_component_in_project(
            project_id_value_object.from_str(project_id),
            component_id_value_object.from_str(component_id),
        )

    def require_version(component_id: str, version_id: str) -> None:
        dependencies.component_version_domain_qry_srv.require_component_version_in_component(
            component_id_value_object.from_str(component_id),
            component_version_id_value_object.from_str(version_id),
        )

    def version_kwargs(request, *, project_id: str, component_id: str) -> dict:
        definition = request.componentVersionDefinition.model_dump(mode="json", by_alias=True, exclude_none=True)
        try:
            yaml_definition = component_version_yaml_definition_value_object.from_dict(definition)
        except DomainException as error:
            common.api_metrics.add_metric(name="StructuredDefinitionValidationFailures", unit=MetricUnit.Count, value=1)
            raise InvalidComponentDefinition() from error
        kwargs = {
            "projectId": project_id_value_object.from_str(project_id),
            "componentId": component_id_value_object.from_str(component_id),
            "componentVersionDescription": component_version_description_value_object.from_str(
                request.componentVersionDescription
            ),
            "componentVersionYamlDefinition": yaml_definition,
            "componentVersionDependencies": component_version_dependencies_value_object.from_list(
                request.componentVersionDependencies or []
            ),
            "softwareVendor": component_software_vendor_value_object.from_str(request.softwareVendor),
            "softwareVersion": component_software_version_value_object.from_str(request.softwareVersion),
        }
        if request.licenseDashboard:
            kwargs["licenseDashboard"] = component_license_dashboard_url_value_object.from_str(request.licenseDashboard)
        if request.notes:
            kwargs["notes"] = component_software_version_notes_value_object.from_str(request.notes)
        return kwargs

    def action_response(version_id: str, status: HTTPStatus) -> api_gateway.Response:
        return api_gateway.Response(
            status_code=status,
            body=api_model.ComponentVersionActionResponse(componentVersionId=version_id),
            headers=({**common.NO_STORE, "Retry-After": "5"} if status == HTTPStatus.ACCEPTED else common.NO_STORE),
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.post("/projects/<project_id>/components/<component_id>/versions")
    def create_component_version(
        project_id: str,
        component_id: str,
        request: api_model.CreateComponentVersionRequest,
    ):
        client_id = authorize(project_id, WRITE_SCOPE)
        require_component(project_id, component_id)
        scope = common.idempotency_scope(
            router,
            client_id,
            project_id,
            "CREATE_COMPONENT_VERSION",
            component_id,
        )
        kwargs = version_kwargs(request, project_id=project_id, component_id=component_id)
        result = idempotency.execute_create(
            service=dependencies.idempotency_service,
            scope=scope,
            request=request,
            resource_id=component_version.generate_version_id(),
            resource_exists=lambda resource_id: dependencies.component_version_qry_srv.get_component_version(
                component_id, resource_id
            )
            is not None,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(
                HTTPStatus.ACCEPTED, {"componentVersionId": resource_id}
            ),
            resume_existing=lambda resource_id: dependencies.resume_component_version_creation(
                component_id, resource_id, kwargs["componentVersionYamlDefinition"].value
            ),
            create=lambda resource_id: create_component_version_response(
                dependencies,
                kwargs,
                request,
                client_id,
                resource_id,
            ),
            now=datetime.now(timezone.utc),
        )
        return api_gateway.Response(
            status_code=result.status_code,
            body=result.body,
            headers={**common.NO_STORE, "Retry-After": "5"},
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/components/<component_id>/versions")
    def list_component_versions(project_id: str, component_id: str):
        authorize(project_id, READ_SCOPE)
        require_component(project_id, component_id)
        versions = dependencies.component_version_domain_qry_srv.get_component_versions(
            component_id_value_object.from_str(component_id)
        )
        return api_model.ComponentVersionPage(
            component_versions=[api_model.ComponentVersion.model_validate(item.model_dump()) for item in versions]
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/components/<component_id>/versions/<version_id>")
    def get_component_version(project_id: str, component_id: str, version_id: str):
        authorize(project_id, READ_SCOPE)
        require_component(project_id, component_id)
        require_version(component_id, version_id)
        component_key = component_id_value_object.from_str(component_id)
        version_key = component_version_id_value_object.from_str(version_id)
        raw_version = dependencies.component_version_qry_srv.get_component_version(component_id, version_id)
        if raw_version.componentVersionS3Uri:
            try:
                version, yaml_definition, _yaml_definition_b64 = (
                    dependencies.component_version_domain_qry_srv.get_component_version(component_key, version_key)
                )
                structured_definition = api_model.ComponentDefinition.model_validate(yaml_definition)
            except (ValidationError, yaml.YAMLError, TypeError, ValueError) as error:
                common.api_metrics.add_metric(
                    name="StoredDefinitionParseFailures",
                    unit=MetricUnit.Count,
                    value=1,
                )
                raise StoredDefinitionInvalid() from error
        else:
            version, yaml_definition = raw_version, None
            structured_definition = None
        return api_model.ComponentVersionResponse(
            component_version=api_model.ComponentVersion.model_validate(version.model_dump()),
            componentVersionDefinition=(structured_definition),
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put("/projects/<project_id>/components/<component_id>/versions/<version_id>")
    def update_component_version(
        project_id: str,
        component_id: str,
        version_id: str,
        request: api_model.UpdateComponentVersionRequest,
    ):
        client_id = authorize(project_id, WRITE_SCOPE)
        require_component(project_id, component_id)
        require_version(component_id, version_id)
        kwargs = version_kwargs(request, project_id=project_id, component_id=component_id)
        kwargs.update(
            componentVersionId=component_version_id_value_object.from_str(version_id),
            lastUpdatedBy=user_id_value_object.from_str(f"service:{client_id}"),
        )
        result = dependencies.command_bus.handle(
            update_component_version_command.UpdateComponentVersionCommand(**kwargs)
        )
        return action_response(result["componentVersionId"], HTTPStatus.ACCEPTED)

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.delete("/projects/<project_id>/components/<component_id>/versions/<version_id>")
    def retire_component_version(project_id: str, component_id: str, version_id: str):
        client_id = authorize(project_id, WRITE_SCOPE)
        require_component(project_id, component_id)
        require_version(component_id, version_id)
        version = dependencies.component_version_qry_srv.get_component_version(component_id, version_id)
        if version.status == component_version.ComponentVersionStatus.Retired:
            return action_response(version_id, HTTPStatus.ACCEPTED)
        result = dependencies.command_bus.handle(
            retire_component_version_command.RetireComponentVersionCommand(
                componentId=component_id_value_object.from_str(component_id),
                componentVersionId=component_version_id_value_object.from_str(version_id),
                serviceAuthorized=True,
                lastUpdatedBy=user_id_value_object.from_str(f"service:{client_id}"),
            )
        )
        return action_response(result["componentVersionId"], HTTPStatus.ACCEPTED)

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.post("/projects/<project_id>/components/<component_id>/versions/<version_id>/release")
    def release_component_version(project_id: str, component_id: str, version_id: str):
        client_id = authorize(project_id, RELEASE_SCOPE)
        require_component(project_id, component_id)
        require_version(component_id, version_id)
        version = dependencies.component_version_qry_srv.get_component_version(component_id, version_id)
        if version.status == component_version.ComponentVersionStatus.Released:
            return action_response(version_id, HTTPStatus.OK)
        result = dependencies.command_bus.handle(
            release_component_version_command.ReleaseComponentVersionCommand(
                projectId=project_id_value_object.from_str(project_id),
                componentId=component_id_value_object.from_str(component_id),
                componentVersionId=component_version_id_value_object.from_str(version_id),
                lastUpdatedBy=user_id_value_object.from_str(f"service:{client_id}"),
            )
        )
        return action_response(result["componentVersionId"], HTTPStatus.OK)

    return router


def create_component_version_response(
    dependencies: bootstrapper.Dependencies,
    kwargs: dict,
    request: api_model.CreateComponentVersionRequest,
    client_id: str,
    version_id: str,
) -> idempotency.StoredCreateResponse:
    kwargs.update(
        componentVersionReleaseType=component_version_release_type_value_object.from_str(
            request.componentVersionReleaseType
        ),
        createdBy=user_id_value_object.from_str(f"service:{client_id}"),
    )
    dependencies.command_bus.handle(
        create_component_version_command.CreateComponentVersionCommand(
            **kwargs,
            componentVersionId=component_version_id_value_object.from_str(version_id),
        )
    )
    return idempotency.StoredCreateResponse(HTTPStatus.ACCEPTED, {"componentVersionId": version_id})
