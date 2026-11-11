from typing import Literal

from pydantic import Field

from app.shared.adapters.message_bus import message_bus


class ProjectGroupAssignmentChanged(message_bus.Message):
    event_name: Literal["ProjectGroupAssignmentChanged"] = Field("ProjectGroupAssignmentChanged", alias="eventName")
    projectId: str
    groupId: str
    roles: list[str]
    version: int
    isDeleted: bool = False
