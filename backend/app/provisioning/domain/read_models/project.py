from typing import Any, Optional

from pydantic import BaseModel, Field


class Project(BaseModel):
    projectId: str = Field(..., title="ProjectId")
    projectName: Optional[str] = Field(None, title="ProjectName")
    projectDescription: Optional[str] = Field(None, title="ProjectDescription")
    # The program's workbench stop rules; None = the deployment's defaults.
    workbenchLifecycle: Optional[dict[str, Any]] = Field(None, title="WorkbenchLifecycle")
