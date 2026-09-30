import logging
from unittest import mock

import pytest

from app.packaging.domain.command_handlers.image import release_base_image_command_handler
from app.packaging.domain.commands.image import release_base_image_command
from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.model.image import image
from app.packaging.domain.model.recipe import base_image_channels, recipe
from app.packaging.domain.ports import base_image_release_service
from app.packaging.domain.value_objects.image import image_id_value_object
from app.packaging.domain.value_objects.shared import project_id_value_object, user_id_value_object

PLATFORM = "proj-platform"
PREFIX = "/vew/base-images"
CHANNELS = base_image_channels.BaseImageChannels(
    releasing_project_id=PLATFORM, parameter_prefix=PREFIX, os_version="Ubuntu 24.04 base"
)


class FakeParameters(base_image_release_service.BaseImageParameterService):
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.history = {name: [value] for name, value in self.values.items()}

    def get_parameter_value(self, name):
        return self.values.get(name)

    def get_parameter_history(self, name):
        return list(self.history.get(name, []))

    def put_parameter_value(self, name, value, description):
        self.values[name] = value
        self.history.setdefault(name, []).append(value)


class FakeTags(base_image_release_service.ImageTagService):
    def __init__(self):
        self.tags = {}

    def set_base_channels(self, ami_id, channels):
        if channels:
            self.tags[ami_id] = set(channels)
        else:
            self.tags.pop(ami_id, None)


def _image(image_id, ami, recipe_id="reci-arm", status=image.ImageStatus.Created):
    return image.Image(
        projectId=PLATFORM,
        imageId=image_id,
        imageBuildVersion=1,
        imageBuildVersionArn="arn:aws:imagebuilder:eu-north-1:1:image/x/1.0.0/1",
        pipelineId="pipe-1",
        pipelineName="base",
        recipeId=recipe_id,
        recipeName="ubuntu-24-04-base-arm64",
        recipeVersionId="vers-1",
        recipeVersionName="1.0.0",
        status=status,
        imageUpstreamId=ami,
        createDate="2026-09-29",
        lastUpdateDate="2026-09-29",
    )


def _recipe(recipe_id, architecture, os_version="Ubuntu 24"):
    return recipe.Recipe(
        projectId=PLATFORM,
        recipeId=recipe_id,
        recipeName="base",
        recipeDescription="base",
        recipePlatform="Linux",
        recipeArchitecture=architecture,
        recipeOsVersion=os_version,
        status=recipe.RecipeStatus.Created,
        createDate="2026-09-29",
        createdBy="x",
        lastUpdateDate="2026-09-29",
        lastUpdatedBy="x",
    )


class World:
    def __init__(self, values=None):
        self.images = {
            "image-arm1": _image("image-arm1", "ami-arm1"),
            "image-arm2": _image("image-arm2", "ami-arm2"),
            "image-amd1": _image("image-amd1", "ami-amd1", recipe_id="reci-amd"),
            "image-fail": _image("image-fail", None, status=image.ImageStatus.Failed),
            "image-onbase": _image("image-onbase", "ami-onbase", recipe_id="reci-onbase"),
        }
        self.recipes = {
            "reci-arm": _recipe("reci-arm", "arm64"),
            "reci-amd": _recipe("reci-amd", "amd64"),
            "reci-onbase": _recipe("reci-onbase", "arm64", "Ubuntu 24.04 base"),
        }
        self.parameters = FakeParameters(values)
        self.tags = FakeTags()

    def release(self, image_id, channel="test", architecture="arm64", project=PLATFORM):
        images = mock.Mock()
        images.get_image.side_effect = lambda project_id, iid: self.images.get(iid) if project_id == PLATFORM else None
        recipes = mock.Mock()
        recipes.get_recipe.side_effect = lambda project_id, rid: self.recipes.get(rid)
        release_base_image_command_handler.handle(
            release_base_image_command.ReleaseBaseImageCommand(
                projectId=project_id_value_object.from_str(project),
                imageId=image_id_value_object.from_str(image_id),
                architecture=architecture,
                channel=channel,
                releasedBy=user_id_value_object.from_str("service:terraform"),
            ),
            channels=CHANNELS,
            image_qry_srv=images,
            recipe_qry_srv=recipes,
            parameter_srv=self.parameters,
            image_tag_srv=self.tags,
            logger=logging.getLogger("test"),
        )


def test_first_release_to_test_writes_the_parameter_and_tags_the_ami():
    world = World()

    world.release("image-arm1")

    assert world.parameters.values == {f"{PREFIX}/test/arm64": "ami-arm1"}
    assert world.tags.tags == {"ami-arm1": {"test"}}


def test_prod_takes_an_image_that_was_released_to_test():
    world = World()
    world.release("image-arm1")

    world.release("image-arm1", "prod")

    assert world.parameters.values[f"{PREFIX}/prod/arm64"] == "ami-arm1"
    assert world.tags.tags == {"ami-arm1": {"test", "prod"}}


def test_prod_rejects_an_image_never_released_to_test():
    world = World()

    with pytest.raises(domain_exception.BaseImageNotReleasedToRequiredChannel):
        world.release("image-arm1", "prod")
    assert world.parameters.values == {}


def test_prod_accepts_an_image_that_left_test_since():
    world = World()
    world.release("image-arm1")
    world.release("image-arm2")

    world.release("image-arm1", "prod")

    assert world.tags.tags == {"ami-arm1": {"prod"}, "ami-arm2": {"test"}}


def test_a_new_release_keeps_the_previous_value_and_moves_the_tag():
    world = World()
    world.release("image-arm1")

    world.release("image-arm2")

    assert world.parameters.values[f"{PREFIX}/test/arm64"] == "ami-arm2"
    assert world.parameters.values[f"{PREFIX}/test/arm64/previous"] == "ami-arm1"
    assert world.tags.tags == {"ami-arm2": {"test"}}


def test_rollback_is_a_release_of_the_older_image():
    world = World()
    world.release("image-arm1")
    world.release("image-arm2")

    world.release("image-arm1")

    assert world.parameters.values[f"{PREFIX}/test/arm64"] == "ami-arm1"
    assert world.parameters.values[f"{PREFIX}/test/arm64/previous"] == "ami-arm2"
    assert world.tags.tags == {"ami-arm1": {"test"}}


def test_releasing_the_same_image_again_changes_nothing():
    world = World()
    world.release("image-arm1")
    history = dict(world.parameters.history)

    world.release("image-arm1")

    assert world.parameters.history == history
    assert f"{PREFIX}/test/arm64/previous" not in world.parameters.values


@pytest.mark.parametrize(
    "image_id,architecture,project",
    [
        ("image-missing", "arm64", PLATFORM),
        ("image-fail", "arm64", PLATFORM),
        ("image-amd1", "arm64", PLATFORM),
        ("image-onbase", "arm64", PLATFORM),
        ("image-arm1", "arm64", "proj-other"),
    ],
)
def test_only_successful_raw_os_builds_of_the_releasing_project_for_the_architecture(image_id, architecture, project):
    world = World()

    with pytest.raises(domain_exception.DomainException):
        world.release(image_id, architecture=architecture, project=project)
    assert world.parameters.values == {}
    assert world.tags.tags == {}


def test_nothing_is_released_while_the_deployment_has_no_releasing_project():
    world = World()
    images, recipes = mock.Mock(), mock.Mock()

    with pytest.raises(domain_exception.ReleasingProjectOnly):
        release_base_image_command_handler.handle(
            release_base_image_command.ReleaseBaseImageCommand(
                projectId=project_id_value_object.from_str(PLATFORM),
                imageId=image_id_value_object.from_str("image-arm1"),
                architecture="arm64",
                channel="test",
                releasedBy=user_id_value_object.from_str("service:terraform"),
            ),
            channels=base_image_channels.BaseImageChannels(),
            image_qry_srv=images,
            recipe_qry_srv=recipes,
            parameter_srv=world.parameters,
            image_tag_srv=world.tags,
            logger=logging.getLogger("test"),
        )
    assert world.parameters.values == {}
