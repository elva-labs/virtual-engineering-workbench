import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, RFC_4122

from app.projects.domain.events.projects.project_created import ProjectCreated
from app.projects.domain.events.projects import project_updated
from app.projects.domain.model import (
    project,
    project_create_request,
    service_client_assignment,
    workbench_lifecycle,
)
from app.shared.adapters.unit_of_work_v2 import unit_of_work
from app.shared.adapters.message_bus import message_bus


class ProjectLifecycleService:
    def __init__(
        self,
        uow: unit_of_work.UnitOfWork,
        query_service,
        events: message_bus.MessageBus,
    ):
        self._uow = uow
        self._query = query_service
        self._events = events

    def create(
        self,
        client_id: str,
        key: str,
        name: str,
        description: str | None,
        is_active: bool,
    ) -> str:
        parsed_key = UUID(key)
        if parsed_key.variant != RFC_4122:
            raise ValueError("Idempotency key must be an RFC 4122 UUID")
        key = str(parsed_key)
        body = json.dumps(
            {"name": name, "description": description, "isActive": is_active},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(body.encode()).hexdigest()
        request_pk = project_create_request.ProjectCreateRequestPrimaryKey(
            clientId=client_id, idempotencyKey=key
        )
        with self._uow:
            requests = self._uow.get_repository(
                project_create_request.ProjectCreateRequestPrimaryKey,
                project_create_request.ProjectCreateRequest,
            )
            existing = requests.get(request_pk)
            if existing:
                if existing.requestHash != digest:
                    raise ValueError("Idempotency key used for a different request")
                project_id = existing.projectId
                existing_project = self._uow.get_repository(
                    project.ProjectPrimaryKey, project.Project
                ).get(project.ProjectPrimaryKey(projectId=project_id))
                if existing_project is None:
                    raise RuntimeError("Reserved project is missing")
                assignment = self._query.get_service_client_assignment(
                    project_id, client_id
                )
                if assignment is None:
                    self._ensure_creator(client_id, project_id, None)
                    self._uow.commit()
                return project_id
            project_id = project.generate_project_id()
            projects = self._uow.get_repository(
                project.ProjectPrimaryKey, project.Project
            )
            if (
                projects.get(project.ProjectPrimaryKey(projectId=project_id))
                is not None
            ):
                raise ValueError("Project ID collision; retry with the same key")
            now = datetime.now(timezone.utc).isoformat()
            projects.add(
                project.Project(
                    projectId=project_id,
                    projectName=name,
                    projectDescription=description,
                    isActive=is_active,
                    createDate=now,
                    lastUpdateDate=now,
                )
            )
            self._ensure_creator(client_id, project_id, None)
            requests.add(
                project_create_request.ProjectCreateRequest(
                    clientId=client_id,
                    idempotencyKey=key,
                    requestHash=digest,
                    projectId=project_id,
                )
            )
            self._uow.commit()
        self._events.publish(
            ProjectCreated(
                projectId=project_id,
                projectName=name,
                projectDescription=description,
                isActive=is_active,
            )
        )
        return project_id

    def _ensure_creator(self, client_id, project_id, current):
        repo = self._uow.get_repository(
            service_client_assignment.ServiceClientAssignmentPrimaryKey,
            service_client_assignment.ServiceClientAssignment,
        )
        now = datetime.now(timezone.utc).isoformat()
        if current is None:
            repo.add(
                service_client_assignment.ServiceClientAssignment(
                    clientId=client_id,
                    projectId=project_id,
                    status=service_client_assignment.ServiceClientAssignmentStatus.ACTIVE,
                    grantedBy=client_id,
                    createDate=now,
                    lastUpdateDate=now,
                )
            )
        else:
            current.status = (
                service_client_assignment.ServiceClientAssignmentStatus.ACTIVE
            )
            current.lastUpdateDate = now
            repo.update_entity(
                service_client_assignment.ServiceClientAssignmentPrimaryKey(
                    clientId=client_id, projectId=project_id
                ),
                current,
            )

    def update(
        self, project_id: str, name: str, description: str | None, is_active: bool
    ):
        with self._uow:
            repo = self._uow.get_repository(project.ProjectPrimaryKey, project.Project)
            pk = project.ProjectPrimaryKey(projectId=project_id)
            current = repo.get(pk)
            if current is None:
                raise KeyError(project_id)
            if (current.projectName, current.projectDescription, current.isActive) == (
                name,
                description,
                is_active,
            ):
                return current
            current.projectName = name
            current.projectDescription = description
            current.isActive = is_active
            current.lastUpdateDate = datetime.now(timezone.utc).isoformat()
            repo.update_entity(pk, current)
            self._uow.commit()
        # The full current state, so an update never drops the management mode.
        self._events.publish(project_updated.from_project(current))
        return current

    def set_workbench_lifecycle(self, project_id: str, settings: workbench_lifecycle.WorkbenchLifecycle | None):
        """Replaces the project's workbench stop policy, or returns it to the deployment's defaults (None).
        The Provisioning BC reads it through the internal projects API, so no event is needed."""
        with self._uow:
            repo = self._uow.get_repository(project.ProjectPrimaryKey, project.Project)
            pk = project.ProjectPrimaryKey(projectId=project_id)
            current = repo.get(pk)
            if current is None:
                raise KeyError(project_id)
            if current.workbenchLifecycle == settings:
                return current
            current.workbenchLifecycle = settings
            current.lastUpdateDate = datetime.now(timezone.utc).isoformat()
            repo.update_entity(pk, current)
            self._uow.commit()
        return current

    def set_management(self, project_id: str, managed_by: str | None, source: str | None):
        """Marks the project as managed by an external tool (the user APIs then refuse its
        configuration changes), or hands it back to the portal (None). Unchanged, it publishes nothing."""
        source = source if managed_by else None
        with self._uow:
            repo = self._uow.get_repository(project.ProjectPrimaryKey, project.Project)
            pk = project.ProjectPrimaryKey(projectId=project_id)
            current = repo.get(pk)
            if current is None:
                raise KeyError(project_id)
            if (current.managedBy, current.managedSource) == (managed_by, source):
                return current
            current.managedBy = managed_by
            current.managedSource = source
            current.lastUpdateDate = datetime.now(timezone.utc).isoformat()
            repo.update_entity(pk, current)
            self._uow.commit()
        self._events.publish(project_updated.from_project(current))
        return current

    def deactivate(self, project_id: str):
        current = self._query.get_project_by_id(project_id)
        if current is None:
            raise KeyError(project_id)
        return self.update(
            project_id, current.projectName, current.projectDescription, False
        )
