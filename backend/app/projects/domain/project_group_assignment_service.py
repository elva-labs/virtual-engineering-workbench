from datetime import datetime, timezone

from app.projects.domain.events.groups.project_group_assignment_changed import ProjectGroupAssignmentChanged
from app.projects.domain.model import project_assignment, project_group_assignment
from app.projects.domain.ports.projects_query_service import ProjectsQueryService
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work


class ProjectGroupAssignmentService:
    def __init__(
        self, uow: unit_of_work.UnitOfWork, query_service: ProjectsQueryService, events: message_bus.MessageBus
    ):
        self._uow = uow
        self._query = query_service
        self._events = events

    def put(self, project_id: str, group_id: str, roles: list[str], group_name: str | None = None):
        if self._query.get_project_by_id(project_id) is None:
            raise ValueError("Project not found")
        validated = list(dict.fromkeys(project_assignment.Role(role) for role in roles))
        current = self._query.get_project_group_assignment(project_id, group_id)
        if (
            current
            and not current.isDeleted
            and set(current.roles) == set(validated)
            and current.groupName == group_name
        ):
            # Republish on retries: the earlier database write may have succeeded
            # even if delivery of its event failed.
            self._publish(current)
            return current
        now = datetime.now(timezone.utc).isoformat()
        assignment = project_group_assignment.ProjectGroupAssignment(
            projectId=project_id,
            groupId=group_id,
            roles=validated,
            groupName=group_name,
            version=current.version + 1 if current else 1,
            createDate=current.createDate if current else now,
            lastUpdateDate=now,
        )
        if current:
            current.roles = assignment.roles
            current.groupName = assignment.groupName
            current.version = assignment.version
            current.isDeleted = False
            current.lastUpdateDate = now
            assignment = current
        self._save(assignment, current is not None)
        return assignment

    def delete(self, project_id: str, group_id: str):
        current = self._query.get_project_group_assignment(project_id, group_id)
        if current is None:
            return
        if current.isDeleted:
            self._publish(current)
            return
        current.version += 1
        current.isDeleted = True
        current.lastUpdateDate = datetime.now(timezone.utc).isoformat()
        self._save(current, True)

    def _save(self, assignment, exists: bool):
        with self._uow:
            repository = self._uow.get_repository(
                project_group_assignment.ProjectGroupAssignmentPrimaryKey,
                project_group_assignment.ProjectGroupAssignment,
            )
            if exists:
                repository.update_entity(
                    project_group_assignment.ProjectGroupAssignmentPrimaryKey(
                        projectId=assignment.projectId, groupId=assignment.groupId
                    ),
                    assignment,
                )
            else:
                repository.add(assignment)
            self._uow.commit()
        self._publish(assignment)

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
