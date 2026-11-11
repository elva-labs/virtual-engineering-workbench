from pydantic import Field

from app.projects.domain.model.project_assignment import Role
from app.shared.adapters.unit_of_work_v2 import unit_of_work


class ProjectGroupAssignmentPrimaryKey(unit_of_work.PrimaryKey):
    projectId: str
    groupId: str


class ProjectGroupAssignment(unit_of_work.Entity):
    projectId: str
    groupId: str = Field(min_length=1, max_length=2042)
    roles: list[Role]
    groupName: str | None = None
    version: int = Field(ge=1)
    isDeleted: bool = False
    createDate: str
    lastUpdateDate: str
