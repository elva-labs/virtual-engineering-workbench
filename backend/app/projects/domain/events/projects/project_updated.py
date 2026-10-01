from typing import Literal, Optional

from pydantic import Field

from app.shared.adapters.message_bus import message_bus


class ProjectUpdated(message_bus.Message):
    event_name: Literal["ProjectUpdated"] = Field("ProjectUpdated", alias="eventName")
    project_id: str = Field(..., alias="projectId")
    project_name: str = Field(..., alias="projectName")
    project_description: Optional[str] = Field(None, alias="projectDescription")
    is_active: bool = Field(..., alias="isActive")
    # Consumed by the Authorization BC to allow or refuse support actions on the project.
    remote_support_enabled: bool = Field(True, alias="remoteSupportEnabled")
    # Consumed by the Authorization BC to refuse user changes to externally managed projects: always the
    # current value, null when the portal owns the configuration.
    managed_by: Optional[str] = Field(None, alias="managedBy")
    managed_source: Optional[str] = Field(None, alias="managedSource")


def from_project(proj) -> ProjectUpdated:
    """The event for the project's current state (app.projects.domain.model.project.Project)."""
    return ProjectUpdated(
        projectId=proj.projectId,
        projectName=proj.projectName,
        projectDescription=proj.projectDescription,
        isActive=proj.isActive,
        remoteSupportEnabled=proj.remoteSupportEnabled,
        managedBy=proj.managedBy,
        managedSource=proj.managedSource,
    )
