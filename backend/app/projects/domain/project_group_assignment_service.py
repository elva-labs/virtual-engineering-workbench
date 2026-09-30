from datetime import datetime, timezone
from uuid import UUID

from app.projects.domain.events.groups.project_group_assignment_changed import (
    ProjectGroupAssignmentChanged,
)
from app.projects.domain.model import project_group_assignment, project_assignment
from app.shared.adapters.unit_of_work_v2 import unit_of_work
from app.shared.adapters.message_bus import message_bus


class ProjectGroupAssignmentService:
    def __init__(
        self,
        uow: unit_of_work.UnitOfWork,
        query_service,
        events: message_bus.MessageBus,
    ):
        self._uow = uow
        self._query = query_service
        self._events = events

    def put(self, project_id: str, group_id: str, roles: list[str]):
        group_id = str(UUID(group_id))
        if self._query.get_project_by_id(project_id) is None:
            raise ValueError("Project not found")
        validated = list(dict.fromkeys(project_assignment.Role(role) for role in roles))
        current = self._query.get_project_group_assignment(project_id, group_id)
        if current and not current.isDeleted and set(current.roles) == set(validated):
            return current
        now = datetime.now(timezone.utc).isoformat()
        assignment = current or project_group_assignment.ProjectGroupAssignment(
            projectId=project_id,
            groupId=group_id,
            roles=validated,
            version=1,
            createDate=now,
            lastUpdateDate=now,
        )
        if current:
            assignment.roles = validated
            assignment.version += 1
            assignment.isDeleted = False
            assignment.lastUpdateDate = now
        with self._uow:
            repository = self._uow.get_repository(
                project_group_assignment.ProjectGroupAssignmentPrimaryKey,
                project_group_assignment.ProjectGroupAssignment,
            )
            if current:
                repository.update_entity(
                    project_group_assignment.ProjectGroupAssignmentPrimaryKey(
                        projectId=project_id, groupId=group_id
                    ),
                    assignment,
                )
            else:
                repository.add(assignment)
            self._uow.commit()
        self._publish(assignment)
        return assignment

    def delete(self, project_id: str, group_id: str):
        group_id = str(UUID(group_id))
        current = self._query.get_project_group_assignment(project_id, group_id)
        if current is None or current.isDeleted:
            return
        current.version += 1
        current.isDeleted = True
        current.lastUpdateDate = datetime.now(timezone.utc).isoformat()
        with self._uow:
            self._uow.get_repository(
                project_group_assignment.ProjectGroupAssignmentPrimaryKey,
                project_group_assignment.ProjectGroupAssignment,
            ).update_entity(
                project_group_assignment.ProjectGroupAssignmentPrimaryKey(
                    projectId=project_id, groupId=group_id
                ),
                current,
            )
            self._uow.commit()
        self._publish(current)

    def _publish(self, assignment):
        self._events.publish(
            ProjectGroupAssignmentChanged(
                projectId=assignment.projectId,
                groupId=assignment.groupId,
                roles=[role.value for role in assignment.roles],
                version=assignment.version,
                isDeleted=assignment.isDeleted,
            )
        )
