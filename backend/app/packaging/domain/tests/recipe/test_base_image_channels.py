"""Base image release channels per recipe version (app/packaging/domain/model/recipe/base_image_channels.py)."""

import json
from unittest import mock

import assertpy
import pytest

from app.packaging.domain.command_handlers.recipe import (
    create_recipe_version_command_handler,
    update_recipe_version_command_handler,
)
from app.packaging.domain.exceptions.domain_exception import DomainException
from app.packaging.domain.model.component import component_version
from app.packaging.domain.model.recipe import base_image_channels, recipe_version
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work

BASE = "Golden Ubuntu"
CONFIG = {"releasingProjectId": "proj-base", "parameterPrefix": "/vew/base-images", "osVersion": BASE}
CHANNELS = base_image_channels.BaseImageChannels.from_dict(CONFIG)
PROD_PARAMETER = "/vew/base-images/prod/amd64"
TEST_PARAMETER = "/vew/base-images/test/amd64"


@pytest.mark.parametrize(
    "os_version,requested,expected",
    [
        (BASE, None, "prod"),
        (BASE, "prod", "prod"),
        (BASE, "test", "test"),
        (f"{BASE} (test)", None, "test"),
        (f"{BASE} (test)", "test", "test"),
        ("Ubuntu 24", None, None),
    ],
)
def test_resolve_channel(os_version, requested, expected):
    assertpy.assert_that(CHANNELS.resolve_channel(os_version, requested)).is_equal_to(expected)


@pytest.mark.parametrize(
    "channels,os_version,requested,message",
    [
        (CHANNELS, "Ubuntu 24", "test", "only applies to recipes on"),
        (CHANNELS, BASE, "beta", "is unknown"),
        (CHANNELS, f"{BASE} (test)", "prod", "always builds on the test channel"),
        (base_image_channels.BaseImageChannels(), "Ubuntu 24", "test", "only applies to recipes on"),
    ],
)
def test_resolve_channel_rejects(channels, os_version, requested, message):
    with pytest.raises(DomainException) as exec_info:
        channels.resolve_channel(os_version, requested)

    assertpy.assert_that(str(exec_info.value)).contains(message)


@pytest.mark.parametrize(
    "os_version,stored,expected",
    [(BASE, None, "prod"), (BASE, "test", "test"), (f"{BASE} (test)", None, "test"), ("Ubuntu 24", None, None)],
)
def test_effective_channel_of_versions_created_before_the_field(os_version, stored, expected):
    assertpy.assert_that(CHANNELS.effective_channel(os_version, stored)).is_equal_to(expected)


def _version_handler_kwargs(handler, command, recipe_qry, parameter_srv, mapping, component_version_qry, version_qry):
    uow_mock = mock.create_autospec(spec=unit_of_work.UnitOfWork)
    uow_mock.get_repository.return_value = mock.create_autospec(spec=unit_of_work.GenericRepository)
    mandatory = mock.Mock()
    mandatory.get_mandatory_components_list.return_value = None
    common = dict(
        command=command,
        uow=uow_mock,
        message_bus=mock.create_autospec(spec=message_bus.MessageBus),
        component_version_qry_srv=component_version_qry,
        mandatory_components_list_qry_srv=mandatory,
        system_configuration_mapping=mapping,
        component_qry_srv=mock.Mock(),
    )
    if handler is create_recipe_version_command_handler:
        return common | dict(recipe_version_qry_srv=version_qry, recipe_qry_srv=recipe_qry, parameter_srv=parameter_srv)
    return common | dict(
        recipe_version_query_service=version_qry, recipe_qry_service=recipe_qry, parameter_qry_srv=parameter_srv
    )


@pytest.fixture
def version_command(request, create_recipe_version_command_mock, update_recipe_version_command_mock):
    return {
        create_recipe_version_command_handler: create_recipe_version_command_mock,
        update_recipe_version_command_handler: update_recipe_version_command_mock,
    }[request.param]


HANDLERS = [create_recipe_version_command_handler, update_recipe_version_command_handler]


@pytest.mark.parametrize(
    "requested,existing,parameter,stored",
    [
        (None, None, PROD_PARAMETER, "prod"),
        ("test", None, TEST_PARAMETER, "test"),
        (None, "test", TEST_PARAMETER, "test"),  # an update keeps the version's own channel
        ("prod", "test", PROD_PARAMETER, "prod"),  # ... or moves it
    ],
)
@pytest.mark.parametrize("handler,version_command", [(h, h) for h in HANDLERS], indirect=["version_command"])
def test_parent_image_comes_from_the_version_channel(
    monkeypatch,
    requested,
    existing,
    parameter,
    stored,
    handler,
    version_command,
    mock_recipe_object,
    mock_recipe_version_object,
    recipe_query_service_mock,
    parameter_service_mock,
    mock_system_configuration_mapping,
    component_version_query_service_mock,
    recipe_version_query_service_mock,
    get_test_component_version_with_specific_status,
):
    if handler is create_recipe_version_command_handler and existing is not None:
        pytest.skip("a new version has no channel of its own")
    monkeypatch.setenv("BASE_IMAGE_CHANNELS", json.dumps(CONFIG))

    def released(component_id, version_id):
        entity = get_test_component_version_with_specific_status(
            status=component_version.ComponentVersionStatus.Released
        )
        entity.componentId, entity.componentVersionId = component_id, version_id
        return entity

    component_version_query_service_mock.get_component_version.side_effect = released
    recipe_query_service_mock.get_recipe.return_value = mock_recipe_object.model_copy(update={"recipeOsVersion": BASE})
    recipe_version_query_service_mock.get_recipe_version.return_value = mock_recipe_version_object.model_copy(
        update={
            "recipeVersionName": "1.0.0-rc.1",
            "status": recipe_version.RecipeVersionStatus.Created,
            "baseImageChannel": existing,
        }
    )
    parameter_service_mock.get_parameter_value.return_value = "ami-0123456789abcdef0"
    kwargs = _version_handler_kwargs(
        handler,
        version_command.model_copy(update={"baseImageChannel": requested}),
        recipe_query_service_mock,
        parameter_service_mock,
        mock_system_configuration_mapping,
        component_version_query_service_mock,
        recipe_version_query_service_mock,
    )

    handler.handle(**kwargs)

    parameter_service_mock.get_parameter_value.assert_called_once_with(parameter)
    repo = kwargs["uow"].get_repository.return_value
    if handler is create_recipe_version_command_handler:
        assertpy.assert_that(repo.add.call_args.args[0].baseImageChannel).is_equal_to(stored)
    else:
        assertpy.assert_that(repo.update_attributes.call_args.kwargs["baseImageChannel"]).is_equal_to(stored)
        # The deploy builds from the event, so it must carry the new parent, not the version's previous one.
        event = kwargs["message_bus"].publish.call_args.args[0]
        assertpy.assert_that(event.parent_image_upstream_id).is_equal_to("ami-0123456789abcdef0")
