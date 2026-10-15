import logging
from datetime import datetime, timezone
from unittest import mock

import assertpy
import pytest

from app.packaging.domain.command_handlers.shared import retire_stuck_versions_command_handler
from app.packaging.domain.model.component import component_version
from app.packaging.domain.model.recipe import recipe_version
from app.packaging.domain.model.shared import recipe_version_entry
from app.packaging.domain.ports import component_version_query_service, recipe_version_query_service

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
STUCK_SINCE = "2026-10-14T08:29:49.334014+00:00"


def _component_version(**overrides) -> component_version.ComponentVersion:
    values = dict(
        componentId="comp-1234abcd",
        componentVersionId="vers-stuck001",
        componentVersionName="3.0.4-rc.1",
        componentName="gpu-drivers",
        componentVersionDescription="GPU drivers",
        componentBuildVersionArn=None,
        componentVersionS3Uri="s3://test/component.yaml",
        componentPlatform="Linux",
        componentSupportedArchitectures=["amd64"],
        componentSupportedOsVersions=["Ubuntu 24"],
        componentVersionDependencies=[],
        softwareVendor="vendor",
        softwareVersion="3.0.4",
        status=component_version.ComponentVersionStatus.Updating,
        createDate=STUCK_SINCE,
        createdBy="T000001",
        lastUpdateDate=STUCK_SINCE,
        lastUpdatedBy="T000001",
    )
    values.update(overrides)
    return component_version.ComponentVersion(**values)


def _recipe_version(**overrides) -> recipe_version.RecipeVersion:
    values = dict(
        recipeId="reci-1234abcd",
        recipeVersionId="vers-recipe01",
        recipeVersionName="1.2.0-rc.1",
        recipeName="gpu-workbench",
        recipeVersionDescription="GPU workbench",
        parentImageUpstreamId="ami-01234567890abcdef",
        recipeComponentsVersions=[],
        recipeVersionVolumeSize="100",
        recipeVersionArn=None,
        recipeVersionComponentArn=None,
        status=recipe_version.RecipeVersionStatus.Updating,
        createDate=STUCK_SINCE,
        createdBy="T000001",
        lastUpdateDate=STUCK_SINCE,
        lastUpdatedBy="T000001",
    )
    values.update(overrides)
    return recipe_version.RecipeVersion(**values)


@pytest.fixture()
def component_qry():
    return mock.create_autospec(spec=component_version_query_service.ComponentVersionQueryService)


@pytest.fixture()
def recipe_qry():
    qry = mock.create_autospec(spec=recipe_version_query_service.RecipeVersionQueryService)
    qry.get_recipe_versions_in_status.return_value = []
    return qry


def _run(uow_mock, component_qry, recipe_qry, now=NOW):
    return retire_stuck_versions_command_handler.handle(
        uow=uow_mock,
        component_version_qry_srv=component_qry,
        recipe_version_qry_srv=recipe_qry,
        logger=mock.create_autospec(spec=logging.Logger),
        now=now,
    )


def test_retires_a_stuck_component_version_without_build_arn(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    stuck = _component_version()
    component_qry.get_component_versions_in_status.return_value = [stuck]
    component_qry.get_component_version.return_value = stuck

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_equal_to(["vers-stuck001"])
    component_qry.get_component_versions_in_status.assert_called_once_with(
        component_version.ComponentVersionStatus.Updating
    )
    generic_repo_mock.update_attributes.assert_called_once_with(
        component_version.ComponentVersionPrimaryKey(componentId="comp-1234abcd", componentVersionId="vers-stuck001"),
        lastUpdateDate=NOW.isoformat(),
        lastUpdatedBy="SYSTEM",
        status=component_version.ComponentVersionStatus.Retired,
    )
    uow_mock.commit.assert_called_once()


def test_leaves_an_updating_component_version_with_a_build_arn(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    # A real update or retirement in flight always has an ARN.
    updating = _component_version(componentBuildVersionArn="arn:aws:imagebuilder:eu-north-1:1:component/x/3.0.4/1")
    component_qry.get_component_versions_in_status.return_value = [updating]
    component_qry.get_component_version.return_value = updating

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_empty()
    generic_repo_mock.update_attributes.assert_not_called()


def test_leaves_a_fresh_component_version(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    fresh = _component_version(lastUpdateDate="2026-10-15T11:50:00+00:00")
    component_qry.get_component_versions_in_status.return_value = [fresh]
    component_qry.get_component_version.return_value = fresh

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_empty()
    generic_repo_mock.update_attributes.assert_not_called()


def test_rereads_and_skips_a_version_that_already_moved_on(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    # The status index is eventually consistent: the consistent read decides.
    component_qry.get_component_versions_in_status.return_value = [_component_version()]
    component_qry.get_component_version.return_value = _component_version(
        status=component_version.ComponentVersionStatus.Retired
    )

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_empty()
    generic_repo_mock.update_attributes.assert_not_called()


def test_keeps_a_component_version_a_live_recipe_still_uses(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    referenced = _component_version(
        associatedRecipesVersions=[
            recipe_version_entry.RecipeVersionEntry(
                recipeId="reci-1234abcd",
                recipeName="gpu-workbench",
                recipeVersionId="vers-recipe01",
                recipeVersionName="1.2.0",
            )
        ]
    )
    component_qry.get_component_versions_in_status.return_value = [referenced]
    component_qry.get_component_version.return_value = referenced
    recipe_qry.get_recipe_version.return_value = _recipe_version(
        status=recipe_version.RecipeVersionStatus.Released, recipeVersionArn="arn:recipe"
    )

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_empty()
    assertpy.assert_that(result["kept"]).is_equal_to(["vers-stuck001"])
    generic_repo_mock.update_attributes.assert_not_called()


def test_a_retired_recipe_reference_does_not_block(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    referenced = _component_version(
        associatedRecipesVersions=[
            recipe_version_entry.RecipeVersionEntry(
                recipeId="reci-1234abcd",
                recipeName="gpu-workbench",
                recipeVersionId="vers-recipe01",
                recipeVersionName="1.2.0",
            )
        ]
    )
    component_qry.get_component_versions_in_status.return_value = [referenced]
    component_qry.get_component_version.return_value = referenced
    recipe_qry.get_recipe_version.return_value = _recipe_version(status=recipe_version.RecipeVersionStatus.Retired)

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_equal_to(["vers-stuck001"])


def test_retires_a_stuck_recipe_version_without_arns(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    component_qry.get_component_versions_in_status.return_value = []
    stuck = _recipe_version()
    recipe_qry.get_recipe_versions_in_status.return_value = [stuck]
    recipe_qry.get_recipe_version.return_value = stuck

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_equal_to(["vers-recipe01"])
    recipe_qry.get_recipe_versions_in_status.assert_called_once_with(recipe_version.RecipeVersionStatus.Updating)
    generic_repo_mock.update_attributes.assert_called_once_with(
        recipe_version.RecipeVersionPrimaryKey(recipeId="reci-1234abcd", recipeVersionId="vers-recipe01"),
        lastUpdateDate=NOW.isoformat(),
        lastUpdatedBy="SYSTEM",
        status=recipe_version.RecipeVersionStatus.Retired,
    )


def test_leaves_an_updating_recipe_version_with_an_arn(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    component_qry.get_component_versions_in_status.return_value = []
    updating = _recipe_version(recipeVersionArn="arn:aws:imagebuilder:eu-north-1:1:image-recipe/x/1.2.0")
    recipe_qry.get_recipe_versions_in_status.return_value = [updating]
    recipe_qry.get_recipe_version.return_value = updating

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result["retired"]).is_empty()
    generic_repo_mock.update_attributes.assert_not_called()


def test_nothing_stuck_does_nothing(uow_mock, generic_repo_mock, component_qry, recipe_qry):
    component_qry.get_component_versions_in_status.return_value = []

    result = _run(uow_mock, component_qry, recipe_qry)

    assertpy.assert_that(result).is_equal_to({"retired": [], "kept": []})
    uow_mock.commit.assert_not_called()
