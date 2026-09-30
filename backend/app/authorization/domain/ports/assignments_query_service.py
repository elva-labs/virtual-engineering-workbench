from abc import ABC, abstractmethod

from app.authorization.domain.read_models import (
    project_assignment,
    project_group_assignment,
    project_settings,
)


class AssignmentsQueryService(ABC):
    @abstractmethod
    def get_user_assignments(self, user_id: str) -> list[project_assignment.Assignment]: ...

    def get_project_settings(self, project_id: str) -> project_settings.ProjectSettings:
        """Settings of a project; the defaults when none were synced yet."""
        return project_settings.ProjectSettings(projectId=project_id)

    @abstractmethod
    def get_project_assignments(self, project_id: str) -> list[project_assignment.Assignment]: ...

    @abstractmethod
    def get_group_assignments(self, group_ids: list[str]) -> list[project_group_assignment.GroupAssignment]: ...
