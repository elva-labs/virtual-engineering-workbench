from typing import Optional

from pydantic import Field

from app.projects.domain.model.project_assignment import Role
from app.shared.adapters.unit_of_work_v2 import unit_of_work


class ProjectGroupAssignmentPrimaryKey(unit_of_work.PrimaryKey):
    projectId: str
    groupId: str


class ProjectGroupAssignment(unit_of_work.Entity):
    projectId: str
    groupId: str
    roles: list[Role]
    # The group's display name, set with the binding (e.g. by Terraform), so VEW needs no directory read.
    groupName: Optional[str] = None
    version: int = Field(ge=1)
    isDeleted: bool = False
    createDate: str
    lastUpdateDate: str
