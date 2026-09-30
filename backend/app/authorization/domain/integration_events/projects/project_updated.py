from typing import Optional

from pydantic import BaseModel, Field


class ProjectUpdated(BaseModel):
    projectId: str = Field(..., alias="projectId")
    # The Projects BC always sends both, null when the portal owns the project; absent in events from
    # before the management mode existed (model_fields_set tells them apart).
    managedBy: Optional[str] = Field(None, alias="managedBy")
    managedSource: Optional[str] = Field(None, alias="managedSource")
