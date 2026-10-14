from typing import Literal, Optional

from pydantic import ConfigDict, Field

from app.packaging.domain.model.shared import component_version_entry
from app.shared.adapters.message_bus import message_bus


class ComponentVersionRetirementStarted(message_bus.Message):
    event_name: Literal["ComponentVersionRetirementStarted"] = Field(
        "ComponentVersionRetirementStarted", alias="eventName"
    )
    component_id: str = Field(..., alias="componentId")
    component_version_id: str = Field(..., alias="componentVersionId")
    # None for a version Image Builder never created; retired without a delete (retire handler).
    component_build_version_arn: Optional[str] = Field(None, alias="componentBuildVersionArn")
    component_version_dependencies: list[component_version_entry.ComponentVersionEntry] = Field(
        list(), alias="componentVersionDependencies"
    )
    model_config = ConfigDict(populate_by_name=True)
