from pydantic import BaseModel, Field

from app.authorization.domain.read_models.project_assignment import Role


class ProjectGroupAssignmentChanged(BaseModel):
    projectId: str
    groupId: str
    roles: list[Role]
    version: int = Field(ge=1)
    isDeleted: bool = False
