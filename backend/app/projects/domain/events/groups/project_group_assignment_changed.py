from typing import Literal
from pydantic import Field
from app.shared.adapters.message_bus import message_bus


class ProjectGroupAssignmentChanged(message_bus.Message):
    event_name: Literal["ProjectGroupAssignmentChanged"] = Field(
        "ProjectGroupAssignmentChanged", alias="eventName"
    )
    project_id: str = Field(..., alias="projectId")
    group_id: str = Field(..., alias="groupId")
    roles: list[str]
    version: int
    is_deleted: bool = Field(..., alias="isDeleted")
