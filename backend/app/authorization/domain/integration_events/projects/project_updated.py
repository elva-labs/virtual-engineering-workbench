from typing import Optional

from pydantic import BaseModel, Field


class ProjectUpdated(BaseModel):
    projectId: str = Field(..., alias="projectId")
    # Absent in events from before the setting existed: leave the stored value alone.
    remoteSupportEnabled: Optional[bool] = Field(None, alias="remoteSupportEnabled")
    # The Projects BC always sends both, null when the portal owns the project; absent in events from
    # before the management mode existed (model_fields_set tells them apart).
    managedBy: Optional[str] = Field(None, alias="managedBy")
    managedSource: Optional[str] = Field(None, alias="managedSource")
