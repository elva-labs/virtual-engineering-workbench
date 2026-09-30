from pydantic import Field

from app.authorization.domain.read_models.project_assignment import Role
from app.shared.adapters.unit_of_work_v2 import unit_of_work


class GroupAssignmentPrimaryKey(unit_of_work.PrimaryKey):
    groupId: str
    projectId: str


class GroupAssignment(unit_of_work.Entity):
    groupId: str
    projectId: str
    roles: list[Role] = Field(default_factory=list)
    version: int = Field(ge=1)
    isDeleted: bool = False
