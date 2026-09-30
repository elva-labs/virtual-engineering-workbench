from app.authorization.domain.integration_events.projects.project_group_assignment_changed import (
    ProjectGroupAssignmentChanged,
)
from app.authorization.domain.read_models.project_group_assignment import (
    GroupAssignment,
    GroupAssignmentPrimaryKey,
)
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def handle(event: ProjectGroupAssignmentChanged, uow: unit_of_work.UnitOfWork):
    with uow:
        repository = uow.get_repository(GroupAssignmentPrimaryKey, GroupAssignment)
        key = GroupAssignmentPrimaryKey(
            projectId=event.projectId, groupId=event.groupId
        )
        existing = repository.get(key)
        if existing is not None and existing.version >= event.version:
            return
        if existing is None:
            repository.add(GroupAssignment(**event.model_dump()))
        else:
            existing.roles = event.roles
            existing.version = event.version
            existing.isDeleted = event.isDeleted
            repository.update_entity(key, existing)
        uow.commit()
