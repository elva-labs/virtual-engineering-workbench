"""Mandatory components lists (docs/api/s2s-contract.md, docs/runbooks/base-images.md).

One list per (platform, OS version, architecture). VEW adds its components to every recipe version
created on that OS - prepended before the recipe's own components, appended after them - so a list for
a base image's OS entry shapes the image of every project building on it. The lists are global: a change
needs the client's assignment to the request's project and, when the deployment has a releasing
project (packaging "base-images" in infra/config.py), must come from that project. PUT upserts, DELETE
is idempotent; the reads are platform-level like the base images: the scope, no project assignment.
"""

from http import HTTPStatus
from urllib.parse import unquote

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types
from aws_lambda_powertools.event_handler.exceptions import NotFoundError

from app.packaging.domain.commands.component import (
    create_mandatory_components_list_command,
    delete_mandatory_components_list_command,
    update_mandatory_components_list_command,
)
from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.exceptions.s2s_exception import MandatoryComponentsListReleasingProjectOnly
from app.packaging.domain.model.component import mandatory_components_list
from app.packaging.domain.model.shared import component_version_entry
from app.packaging.domain.value_objects.component import (
    component_platform_value_object,
    component_supported_architecture_value_object,
    component_supported_os_version_value_object,
)
from app.packaging.domain.value_objects.component_version import components_versions_list_value_object
from app.packaging.domain.value_objects.shared import user_id_value_object
from app.packaging.entrypoints.s2s_api import bootstrapper
from app.packaging.entrypoints.s2s_api.model import api_model
from app.packaging.entrypoints.s2s_api.routers import common

tracer = Tracer()

READ_SCOPE = "clients/packaging/mandatory_components_list.read"
WRITE_SCOPE = "clients/packaging/mandatory_components_list.write"
PATH = "/mandatory-components-lists/<platform>/<os_version>/<architecture>"


def _key(platform: str, os_version: str, architecture: str) -> tuple[str, str, str]:
    # The OS version carries spaces and parentheses ("Ubuntu 24"); unquote is a
    # no-op when API Gateway already decoded the segment.
    platform, os_version, architecture = (unquote(value) for value in (platform, os_version, architecture))
    try:
        return (
            component_platform_value_object.from_str(platform).value,
            component_supported_os_version_value_object.from_str(os_version).value,
            component_supported_architecture_value_object.from_str(architecture).value,
        )
    except domain_exception.DomainException as error:
        raise NotFoundError(str(error)) from error


def _response_body(entity: mandatory_components_list.MandatoryComponentsList) -> api_model.MandatoryComponentsList:
    def versions(position: str) -> list[api_model.MandatoryComponentVersion]:
        entries = [entry for entry in entity.mandatoryComponentsVersions if entry.position == position]
        return [
            api_model.MandatoryComponentVersion(
                componentId=entry.componentId,
                componentName=entry.componentName,
                componentVersionId=entry.componentVersionId,
                componentVersionName=entry.componentVersionName,
                order=entry.order,
            )
            for entry in sorted(entries, key=lambda entry: entry.order or 0)
        ]

    return api_model.MandatoryComponentsList(
        platform=entity.mandatoryComponentsListPlatform,
        osVersion=entity.mandatoryComponentsListOsVersion,
        architecture=entity.mandatoryComponentsListArchitecture,
        prependedComponentsVersions=versions(component_version_entry.ComponentVersionEntryPosition.Prepend),
        appendedComponentsVersions=versions(component_version_entry.ComponentVersionEntryPosition.Append),
        lastUpdateDate=entity.lastUpdateDate,
        lastUpdatedBy=entity.lastUpdatedBy,
    )


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:
    router = api_gateway.Router()

    def get_list(key: tuple[str, str, str]) -> mandatory_components_list.MandatoryComponentsList | None:
        platform, os_version, architecture = key
        return dependencies.mandatory_components_list_qry_srv.get_mandatory_components_list(
            platform=platform, os=os_version, architecture=architecture
        )

    def entries(
        refs: list[api_model.MandatoryComponentVersionRef],
    ) -> list[component_version_entry.ComponentVersionEntry]:
        resolved = []
        for ref in refs:
            version = dependencies.component_version_qry_srv.get_component_version(
                component_id=ref.componentId, version_id=ref.componentVersionId
            )
            if version is None:
                raise domain_exception.DomainException(
                    f"Version {ref.componentVersionId} of component {ref.componentId} does not exist."
                )
            resolved.append(
                component_version_entry.ComponentVersionEntry(
                    componentId=version.componentId,
                    componentName=version.componentName,
                    componentVersionId=version.componentVersionId,
                    componentVersionName=version.componentVersionName,
                )
            )
        return resolved

    def require_platform_program(project_id: str) -> str:
        releasing = dependencies.base_image_channels.releasing_project_id
        if not project_id or (releasing and project_id != releasing):
            raise MandatoryComponentsListReleasingProjectOnly()
        client_id = common.client_id(router)
        dependencies.project_access_service.require_access(client_id, project_id)
        return client_id

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/mandatory-components-lists")
    def list_mandatory_components_lists():
        common.require_scope(router, READ_SCOPE)
        lists = dependencies.mandatory_components_list_qry_srv.get_mandatory_components_lists()
        return api_model.MandatoryComponentsListPage(
            mandatoryComponentsLists=[
                _response_body(entity)
                for entity in sorted(
                    lists,
                    key=lambda entity: (
                        entity.mandatoryComponentsListPlatform,
                        entity.mandatoryComponentsListOsVersion,
                        entity.mandatoryComponentsListArchitecture,
                    ),
                )
            ]
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get(PATH)
    def get_mandatory_components_list(platform: str, os_version: str, architecture: str):
        common.require_scope(router, READ_SCOPE)
        key = _key(platform, os_version, architecture)
        entity = get_list(key)
        if entity is None:
            raise NotFoundError(f"No mandatory components list for {key[0]} {key[1]} ({key[2]}).")
        return api_gateway.Response(
            status_code=HTTPStatus.OK,
            body=_response_body(entity),
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put(PATH)
    def put_mandatory_components_list(
        platform: str, os_version: str, architecture: str, request: api_model.PutMandatoryComponentsListRequest
    ):
        key = _key(platform, os_version, architecture)
        client_id = require_platform_program(request.projectId)
        common.require_scope(router, WRITE_SCOPE)
        values = {
            "mandatoryComponentsListPlatform": component_platform_value_object.from_str(key[0]),
            "mandatoryComponentsListOsVersion": component_supported_os_version_value_object.from_str(key[1]),
            "mandatoryComponentsListArchitecture": component_supported_architecture_value_object.from_str(key[2]),
            "prependedComponentsVersions": components_versions_list_value_object.from_list(
                entries(request.prependedComponentsVersions)
            ),
            "appendedComponentsVersions": components_versions_list_value_object.from_list(
                entries(request.appendedComponentsVersions)
            ),
        }
        actor = user_id_value_object.from_str(f"service:{client_id}")
        if get_list(key) is None:
            command = create_mandatory_components_list_command.CreateMandatoryComponentsListCommand(
                **values, createdBy=actor
            )
        else:
            command = update_mandatory_components_list_command.UpdateMandatoryComponentsListCommand(
                **values, lastUpdatedBy=actor
            )
        dependencies.command_bus.handle(command)
        return api_gateway.Response(
            status_code=HTTPStatus.OK,
            body=_response_body(get_list(key)),
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.delete(PATH)
    def delete_mandatory_components_list(platform: str, os_version: str, architecture: str):
        key = _key(platform, os_version, architecture)
        project_id = router.current_event.get_query_string_value("projectId", "") or ""
        require_platform_program(project_id)
        common.require_scope(router, WRITE_SCOPE)
        dependencies.command_bus.handle(
            delete_mandatory_components_list_command.DeleteMandatoryComponentsListCommand(
                mandatoryComponentsListPlatform=component_platform_value_object.from_str(key[0]),
                mandatoryComponentsListOsVersion=component_supported_os_version_value_object.from_str(key[1]),
                mandatoryComponentsListArchitecture=component_supported_architecture_value_object.from_str(key[2]),
            )
        )
        return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT, body="", headers=common.NO_STORE)

    return router
