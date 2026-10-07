import hashlib
import json
from datetime import datetime, timezone
from uuid import RFC_4122, UUID

from app.projects.domain.events.projects import project_updated
from app.projects.domain.events.projects.project_created import ProjectCreated
from app.projects.domain.model import (
    project,
    project_create_request,
    service_client_assignment,
    workbench_lifecycle,
)
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work


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
        remote_support_enabled: bool | None = None,
        experience: str | None = None,
    ) -> str:
        parsed_key = UUID(key)
        if parsed_key.variant != RFC_4122:
            raise ValueError("Idempotency key must be an RFC 4122 UUID")
        key = str(parsed_key)
        fields = {"name": name, "description": description, "isActive": is_active}
        if remote_support_enabled is not None:
            # Only when given, so requests from before the setting existed keep their hash.
            fields["remoteSupportEnabled"] = remote_support_enabled
        if experience is not None:
            fields["experience"] = experience
        body = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(body.encode()).hexdigest()
        request_pk = project_create_request.ProjectCreateRequestPrimaryKey(clientId=client_id, idempotencyKey=key)
        with self._uow:
            create_requests = self._uow.get_repository(
                project_create_request.ProjectCreateRequestPrimaryKey,
                project_create_request.ProjectCreateRequest,
            )
            existing = create_requests.get(request_pk)
            if existing:
                return self._replay(existing, digest, client_id)
            project_id = project.generate_project_id()
            projects = self._uow.get_repository(project.ProjectPrimaryKey, project.Project)
            if projects.get(project.ProjectPrimaryKey(projectId=project_id)) is not None:
                raise ValueError("Project ID collision; retry with the same key")
            now = datetime.now(timezone.utc).isoformat()
            projects.add(
                project.Project(
                    projectId=project_id,
                    projectName=name,
                    projectDescription=description,
                    isActive=is_active,
                    # Remote support is on unless the caller turns it off.
                    remoteSupportEnabled=True if remote_support_enabled is None else remote_support_enabled,
                    # "full" unless the caller asks for workbench-only.
                    experience=experience or project.EXPERIENCE_FULL,
                    createDate=now,
                    lastUpdateDate=now,
                )
            )
            self._ensure_creator(client_id, project_id, None)
            create_requests.add(
                project_create_request.ProjectCreateRequest(
                    clientId=client_id,
                    idempotencyKey=key,
                    requestHash=digest,
                    projectId=project_id,
                )
            )
            self._uow.commit()
        self._publish_created(project_id, name, description, is_active, remote_support_enabled, experience)
        return project_id

    def _replay(self, existing, digest: str, client_id: str) -> str:
        """A retried create: the same request returns the reserved project; another one is refused."""
        if existing.requestHash != digest:
            raise ValueError("Idempotency key used for a different request")
        project_id = existing.projectId
        existing_project = self._uow.get_repository(project.ProjectPrimaryKey, project.Project).get(
            project.ProjectPrimaryKey(projectId=project_id)
        )
        if existing_project is None:
            raise RuntimeError("Reserved project is missing")
        assignment = self._query.get_service_client_assignment(project_id, client_id)
        if assignment is None:
            self._ensure_creator(client_id, project_id, None)
            self._uow.commit()
        return project_id

    def _publish_created(self, project_id, name, description, is_active, remote_support_enabled, experience=None):
        self._events.publish(
            ProjectCreated(
                projectId=project_id,
                projectName=name,
                projectDescription=description,
                isActive=is_active,
            )
        )
        if remote_support_enabled is False or (experience or project.EXPERIENCE_FULL) != project.EXPERIENCE_FULL:
            # The Authorization BC assumes remote support is on and the experience is "full" until told
            # otherwise (ProjectUpdated).
            created = self._query.get_project_by_id(project_id)
            if created is not None:
                self._events.publish(project_updated.from_project(created))

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
            current.status = service_client_assignment.ServiceClientAssignmentStatus.ACTIVE
            current.lastUpdateDate = now
            repo.update_entity(
                service_client_assignment.ServiceClientAssignmentPrimaryKey(clientId=client_id, projectId=project_id),
                current,
            )

    def update(
        self,
        project_id: str,
        name: str,
        description: str | None,
        is_active: bool,
        remote_support_enabled: bool | None = None,
        experience: str | None = None,
    ):
        with self._uow:
            repo = self._uow.get_repository(project.ProjectPrimaryKey, project.Project)
            pk = project.ProjectPrimaryKey(projectId=project_id)
            current = repo.get(pk)
            if current is None:
                raise KeyError(project_id)
            # Omitted, the remote-support setting keeps its value.
            remote_support = current.remoteSupportEnabled if remote_support_enabled is None else remote_support_enabled
            # Experience omitted keeps its value as well.
            new_experience = current.experience if experience is None else experience
            if (
                current.projectName,
                current.projectDescription,
                current.isActive,
                current.remoteSupportEnabled,
                current.experience,
            ) == (
                name,
                description,
                is_active,
                remote_support,
                new_experience,
            ):
                return current
            current.projectName = name
            current.projectDescription = description
            current.isActive = is_active
            current.remoteSupportEnabled = remote_support
            current.experience = new_experience
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
        return self.update(project_id, current.projectName, current.projectDescription, False)
