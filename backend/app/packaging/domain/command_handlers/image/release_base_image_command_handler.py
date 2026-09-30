"""Releases a build of the releasing project as a base image (app/packaging/domain/model/recipe/base_image_channels.py).

The channel's parameter gets the build's AMI; the value it replaces is kept in <parameter>/previous, so a
rollback is a release of the older image. The AMIs are tagged vew:base-channel with the channels they
serve. Releasing the image a channel already has changes nothing (the tags are reconciled anyway).
"""

import logging

from app.packaging.domain.commands.image import release_base_image_command
from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.model.image import image
from app.packaging.domain.model.recipe import base_image_channels
from app.packaging.domain.ports import base_image_release_service, image_query_service, recipe_query_service


def _releasable_ami(
    command: release_base_image_command.ReleaseBaseImageCommand,
    channels: base_image_channels.BaseImageChannels,
    image_qry_srv: image_query_service.ImageQueryService,
    recipe_qry_srv: recipe_query_service.RecipeQueryService,
) -> tuple[str, str]:
    project_id, image_id = command.projectId.value, command.imageId.value
    build = image_qry_srv.get_image(project_id, image_id)
    if build is None:
        raise domain_exception.DomainException(f"Image {image_id} is not a build of project {project_id}.")
    if build.status != image.ImageStatus.Created or not (build.imageUpstreamId or "").startswith("ami-"):
        raise domain_exception.DomainException(
            f"Image {image_id} is not a successful build (status {build.status}); only a created AMI can be released."
        )
    recipe = recipe_qry_srv.get_recipe(project_id, build.recipeId)
    if recipe is None:
        raise domain_exception.DomainException(f"The recipe {build.recipeId} of image {image_id} does not exist.")
    if recipe.recipeArchitecture != command.architecture:
        raise domain_exception.DomainException(
            f"Image {image_id} is a {recipe.recipeArchitecture} build; it cannot be the {command.architecture} base."
        )
    if recipe.recipeOsVersion in channels.os_versions:
        raise domain_exception.DomainException(
            f"Image {image_id} is built on {recipe.recipeOsVersion}; a base image is built on the raw OS."
        )
    return build.imageUpstreamId, f"{build.recipeName} {build.recipeVersionName}"


def handle(
    command: release_base_image_command.ReleaseBaseImageCommand,
    channels: base_image_channels.BaseImageChannels,
    image_qry_srv: image_query_service.ImageQueryService,
    recipe_qry_srv: recipe_query_service.RecipeQueryService,
    parameter_srv: base_image_release_service.BaseImageParameterService,
    image_tag_srv: base_image_release_service.ImageTagService,
    logger: logging.Logger,
) -> None:
    if not channels.enabled or command.projectId.value != channels.releasing_project_id:
        raise domain_exception.ReleasingProjectOnly("Only the releasing project releases base images.")
    arch, channel = command.architecture, command.channel
    parameters = channels.parameters()
    parameter_name = parameters.get((channel, arch))
    if parameter_name is None:
        raise domain_exception.DomainException(f"There is no {channel} base image channel for {arch}.")

    ami_id, source = _releasable_ami(command, channels, image_qry_srv, recipe_qry_srv)
    required = base_image_channels.REQUIRES.get(channel)
    if required and ami_id not in parameter_srv.get_parameter_history(parameters[(required, arch)]):
        raise domain_exception.BaseImageNotReleasedToRequiredChannel(
            f"Image {command.imageId.value} ({ami_id}) has never been released to {required} for {arch}; release it "
            f"to {required} first."
        )

    current = parameter_srv.get_parameter_value(parameter_name)
    if current != ami_id:
        description = f"{channels.os_version_of(channel)}: {command.imageId.value} ({source})"
        if current:
            parameter_srv.put_parameter_value(
                f"{parameter_name}/previous", current, f"{channel} base image before {command.imageId.value}"
            )
        parameter_srv.put_parameter_value(parameter_name, ami_id, description)
        logger.info(f"{parameter_name}: {current} -> {ami_id} ({command.imageId.value}, {command.releasedBy.value}).")

    def channels_of(ami: str) -> set[str]:
        return {
            c
            for (c, a), name in parameters.items()
            if a == arch and (ami_id if name == parameter_name else parameter_srv.get_parameter_value(name)) == ami
        }

    image_tag_srv.set_base_channels(ami_id, channels_of(ami_id))
    if current and current != ami_id:
        image_tag_srv.set_base_channels(current, channels_of(current))
