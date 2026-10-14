from typing import Literal, Optional

from pydantic import ConfigDict, Field

from app.packaging.domain.model.shared import component_version_entry
from app.shared.adapters.message_bus import message_bus


class RecipeVersionRetirementStarted(message_bus.Message):
    event_name: Literal["RecipeVersionRetirementStarted"] = Field("RecipeVersionRetirementStarted", alias="eventName")
    project_id: str = Field(..., alias="projectId")
    recipe_id: str = Field(..., alias="recipeId")
    recipe_name: str = Field(..., alias="recipeName")
    recipe_version_id: str = Field(..., alias="recipeVersionId")
    # None for a version Image Builder never created (a failed deploy): retired without a delete.
    recipe_version_arn: Optional[str] = Field(None, alias="recipeVersionArn")
    recipe_version_component_arn: Optional[str] = Field(None, alias="recipeVersionComponentArn")
    recipe_version_name: str = Field(..., alias="recipeVersionName")
    recipe_component_versions: list[component_version_entry.ComponentVersionEntry] = Field(
        ..., alias="recipeComponentsVersions"
    )
    last_updated_by: str = Field(..., alias="lastUpdatedBy")
    model_config = ConfigDict(populate_by_name=True)
