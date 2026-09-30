"""Base image release channels (app/packaging/domain/model/recipe/base_image_channels.py).

Reads are deployment-level (the same for every project): the scope, no project assignment. A release
(PUT) needs the releasing project's assignment and the write scope; prod takes only an image that is or
was released to test. Repeating a release changes nothing.
"""

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types
from aws_lambda_powertools.event_handler.exceptions import NotFoundError

from app.packaging.domain.commands.image import release_base_image_command
from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.exceptions.s2s_exception import BaseImageNotReleasedToRequiredChannel, ReleasingProjectOnly
from app.packaging.domain.value_objects.image import image_id_value_object
from app.packaging.domain.value_objects.shared import project_id_value_object, user_id_value_object
from app.packaging.entrypoints.s2s_api import bootstrapper
from app.packaging.entrypoints.s2s_api.model import api_model
from app.packaging.entrypoints.s2s_api.routers import common

tracer = Tracer()

READ_SCOPE = "clients/packaging/base_image.read"
WRITE_SCOPE = "clients/packaging/base_image.write"


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()
    channels = dependencies.base_image_channels

    def base_image(channel: str, architecture: str, parameter_name: str) -> api_model.BaseImage:
        parameters = dependencies.base_image_parameter_service
        ami_id = parameters.get_parameter_value(parameter_name) if parameters else None
        build = dependencies.image_domain_qry_srv.get_image_by_image_upstream_id(ami_id) if ami_id else None
        return api_model.BaseImage(
            architecture=architecture,
            channel=channel,
            osVersion=channels.os_version_of(channel),
            parameterName=parameter_name,
            status=api_model.BaseImageStatus.RELEASED if ami_id else api_model.BaseImageStatus.NOT_RELEASED,
            projectId=channels.releasing_project_id,
            imageId=build.imageId if build is not None and build.projectId == channels.releasing_project_id else None,
            amiId=ami_id,
            previousAmiId=parameters.get_parameter_value(f"{parameter_name}/previous") if ami_id else None,
        )

    def page(architecture: str | None = None) -> api_model.BaseImagePage:
        return api_model.BaseImagePage(
            baseImages=[
                base_image(channel, arch, name)
                for (channel, arch), name in sorted(channels.parameters().items())
                if architecture in (None, arch)
            ]
        )

    def parameter_name(architecture: str, channel: str) -> str:
        name = channels.parameters().get((channel, architecture))
        if name is None:
            raise NotFoundError(f"No {channel} base image channel for architecture {architecture}.")
        return name

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/base-images")
    def list_base_images():
        common.require_scope(router, READ_SCOPE)
        return page()

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/base-images/<architecture>")
    def list_architecture_base_images(architecture: str):
        common.require_scope(router, READ_SCOPE)
        result = page(architecture)
        if not result.baseImages:
            raise NotFoundError(f"No base image for architecture {architecture}.")
        return result

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/base-images/<architecture>/<channel>")
    def get_base_image(architecture: str, channel: str):
        common.require_scope(router, READ_SCOPE)
        return base_image(channel, architecture, parameter_name(architecture, channel))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put("/base-images/<architecture>/<channel>")
    def release_base_image(architecture: str, channel: str, request: api_model.ReleaseBaseImageRequest):
        name = parameter_name(architecture, channel)
        if not channels.enabled or request.projectId != channels.releasing_project_id:
            raise ReleasingProjectOnly()
        client_id = common.client_id(router)
        dependencies.project_access_service.require_access(client_id, channels.releasing_project_id)
        common.require_scope(router, WRITE_SCOPE)
        try:
            dependencies.command_bus.handle(
                release_base_image_command.ReleaseBaseImageCommand(
                    projectId=project_id_value_object.from_str(channels.releasing_project_id),
                    imageId=image_id_value_object.from_str(request.imageId),
                    architecture=architecture,
                    channel=channel,
                    releasedBy=user_id_value_object.from_str(f"service:{client_id}"),
                )
            )
        except domain_exception.BaseImageNotReleasedToRequiredChannel as error:
            raise BaseImageNotReleasedToRequiredChannel(str(error)) from error
        return api_gateway.Response(
            status_code=200,
            body=base_image(channel, architecture, name),
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    return router
