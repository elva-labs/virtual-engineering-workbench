from abc import ABC, abstractmethod

from app.authorization.domain.read_models import (
    project_assignment,
    project_group_assignment,
)


class AssignmentsQueryService(ABC):
    @abstractmethod
    def get_user_assignments(self, user_id: str) -> list[project_assignment.Assignment]: ...

    @abstractmethod
    def get_project_assignments(self, project_id: str) -> list[project_assignment.Assignment]: ...

    @abstractmethod
    def get_group_assignments(self, group_ids: list[str]) -> list[project_group_assignment.GroupAssignment]: ...
