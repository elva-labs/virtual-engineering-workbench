"""Retires component and recipe versions stuck in UPDATING that Image Builder never created.

A version whose create failed has no build ARN. Its retirement could set it to UPDATING and then fail to publish
the retirement event, so nothing moved it on. A client that treats UPDATING as a retirement already in flight (the
Terraform provider) then only polls it and never sends DELETE again, so the retire path that accepts such a
version is never reached. This hourly reconcile finishes those retirements itself.

Only a version that is UPDATING, has no ARN and hasn't changed for STALE_AFTER is touched: an update in flight
always has an ARN (only RELEASED or VALIDATED versions can be updated), and a fresh retirement finishes within
seconds. Nothing is deleted in Image Builder, since nothing was created there. A version that a live recipe or
component version still references is left alone and logged.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.packaging.domain.model.component import component_version
from app.packaging.domain.model.recipe import recipe_version
from app.packaging.domain.ports import component_version_query_service, recipe_version_query_service
from app.shared.adapters.unit_of_work_v2.unit_of_work import UnitOfWork

STALE_AFTER = timedelta(minutes=30)
RECONCILE_USER = "SYSTEM"


def _is_stale(last_update_date: str, now: datetime) -> bool:
    try:
        updated = datetime.fromisoformat(last_update_date)
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return now - updated >= STALE_AFTER


def _live_component_reference(
    entity: component_version.ComponentVersion,
    component_version_qry_srv: component_version_query_service.ComponentVersionQueryService,
    recipe_version_qry_srv: recipe_version_query_service.RecipeVersionQueryService,
) -> str | None:
    for entry in entity.associatedRecipesVersions or []:
        associated = recipe_version_qry_srv.get_recipe_version(
            recipe_id=entry.recipeId, version_id=entry.recipeVersionId
        )
        if associated is not None and associated.status != recipe_version.RecipeVersionStatus.Retired:
            return f"recipe {entry.recipeId} version {entry.recipeVersionId}"
    for entry in entity.associatedComponentsVersions or []:
        associated = component_version_qry_srv.get_component_version(
            component_id=entry.componentId, version_id=entry.componentVersionId
        )
        if associated is not None and associated.status != component_version.ComponentVersionStatus.Retired:
            return f"component {entry.componentId} version {entry.componentVersionId}"
    return None


def handle(
    uow: UnitOfWork,
    component_version_qry_srv: component_version_query_service.ComponentVersionQueryService,
    recipe_version_qry_srv: recipe_version_query_service.RecipeVersionQueryService,
    logger: logging.Logger,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    retired: list[str] = []
    kept: list[str] = []

    # The status index is eventually consistent: each candidate is read again before it is changed.
    for summary in component_version_qry_srv.get_component_versions_in_status(
        component_version.ComponentVersionStatus.Updating
    ):
        entity = component_version_qry_srv.get_component_version(
            component_id=summary.componentId, version_id=summary.componentVersionId
        )
        if (
            entity is None
            or entity.status != component_version.ComponentVersionStatus.Updating
            or entity.componentBuildVersionArn
            or not _is_stale(entity.lastUpdateDate, now)
        ):
            continue
        reference = _live_component_reference(entity, component_version_qry_srv, recipe_version_qry_srv)
        if reference:
            logger.warning(
                f"Component {entity.componentId} version {entity.componentVersionId} is stuck in UPDATING "
                f"without a build ARN but {reference} still uses it: not retired"
            )
            kept.append(entity.componentVersionId)
            continue
        with uow:
            uow.get_repository(
                component_version.ComponentVersionPrimaryKey, component_version.ComponentVersion
            ).update_attributes(
                component_version.ComponentVersionPrimaryKey(
                    componentId=entity.componentId, componentVersionId=entity.componentVersionId
                ),
                lastUpdateDate=now.isoformat(),
                lastUpdatedBy=RECONCILE_USER,
                status=component_version.ComponentVersionStatus.Retired,
            )
            uow.commit()
        logger.info(
            f"Retired component {entity.componentId} version {entity.componentVersionId}: stuck in UPDATING "
            f"since {entity.lastUpdateDate} without a build ARN"
        )
        retired.append(entity.componentVersionId)

    for summary in recipe_version_qry_srv.get_recipe_versions_in_status(recipe_version.RecipeVersionStatus.Updating):
        entity = recipe_version_qry_srv.get_recipe_version(
            recipe_id=summary.recipeId, version_id=summary.recipeVersionId
        )
        if (
            entity is None
            or entity.status != recipe_version.RecipeVersionStatus.Updating
            or entity.recipeVersionArn
            or entity.recipeVersionComponentArn
            or not _is_stale(entity.lastUpdateDate, now)
        ):
            continue
        with uow:
            uow.get_repository(recipe_version.RecipeVersionPrimaryKey, recipe_version.RecipeVersion).update_attributes(
                recipe_version.RecipeVersionPrimaryKey(
                    recipeId=entity.recipeId, recipeVersionId=entity.recipeVersionId
                ),
                lastUpdateDate=now.isoformat(),
                lastUpdatedBy=RECONCILE_USER,
                status=recipe_version.RecipeVersionStatus.Retired,
            )
            uow.commit()
        logger.info(
            f"Retired recipe {entity.recipeId} version {entity.recipeVersionId}: stuck in UPDATING "
            f"since {entity.lastUpdateDate} without a recipe ARN"
        )
        retired.append(entity.recipeVersionId)

    return {"retired": retired, "kept": kept}
