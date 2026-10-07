from http import HTTPStatus
from uuid import RFC_4122, UUID

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.exceptions import BadRequestError, NotFoundError, ServiceError

from app.projects.domain.model import workbench_lifecycle
from app.projects.entrypoints.s2s_api import bootstrapper
from app.projects.entrypoints.s2s_api.model import api_model
from app.projects.entrypoints.s2s_api.routers import access

tracer = Tracer()


def init(dependencies: bootstrapper.Dependencies) -> Router:  # noqa: C901
    router = Router()

    @router.get("/projects")
    def get_projects() -> api_model.GetProjectsResponse:
        access.require_scope(router, "clients/projects/program.read")
        page_size = int(router.current_event.get_query_string_value("pageSize") or 10)
        next_token = router.current_event.get_query_string_value("nextToken")
        projects, last_evaluated_key, _ = dependencies.projects_query_service.list_projects(page_size, next_token, None)
        return api_model.GetProjectsResponse(
            projects=[api_model.Project.model_validate(p.model_dump()) for p in projects],
            nextToken=last_evaluated_key,
        )

    @router.post("/projects")
    def create_project(request: api_model.ProjectMutationRequest):
        access.require_scope(router, "clients/projects/program.write")
        key = router.current_event.get_header_value("Idempotency-Key")
        if key is None:
            raise BadRequestError("Idempotency-Key is required")
        try:
            if UUID(key).variant != RFC_4122:
                raise ValueError("Invalid UUID variant")
        except ValueError as exc:
            raise BadRequestError("Idempotency-Key must be a UUID") from exc
        try:
            project_id = dependencies.project_lifecycle_service.create(
                router.context["user_principal"].user_name,
                key,
                request.name,
                request.description,
                request.isActive,
                request.remoteSupportEnabled,
                # A workbench-only program: members launch workbenches only, from PROD releases.
                request.experience,
            )
        except ValueError as exc:
            raise ServiceError(409, str(exc)) from exc
        return api_gateway.Response(
            status_code=HTTPStatus.CREATED,
            body=api_model.CreateProjectResponse(projectId=project_id),
            content_type=content_types.APPLICATION_JSON,
        )

    @router.get("/projects/<project_id>")
    def get_project(project_id: str):
        access.require_project_access(
            router,
            dependencies.projects_query_service,
            project_id,
            "clients/projects/program.read",
        )
        current = dependencies.projects_query_service.get_project_by_id(project_id)
        if current is None:
            raise NotFoundError("Project not found")
        return api_model.Project.model_validate(current.model_dump())

    @router.put("/projects/<project_id>")
    def update_project(request: api_model.ProjectMutationRequest, project_id: str):
        access.require_project_access(
            router,
            dependencies.projects_query_service,
            project_id,
            "clients/projects/program.write",
        )
        try:
            updated = dependencies.project_lifecycle_service.update(
                project_id,
                request.name,
                request.description,
                request.isActive,
                request.remoteSupportEnabled,
                request.experience,
            )
        except KeyError as exc:
            raise NotFoundError("Project not found") from exc
        return api_model.Project.model_validate(updated.model_dump())

    @router.delete("/projects/<project_id>")
    def deactivate_project(project_id: str):
        access.require_project_access(
            router,
            dependencies.projects_query_service,
            project_id,
            "clients/projects/program.write",
        )
        try:
            updated = dependencies.project_lifecycle_service.deactivate(project_id)
        except KeyError as exc:
            raise NotFoundError("Project not found") from exc
        return api_model.Project.model_validate(updated.model_dump())

    def existing_project(project_id: str):
        current = dependencies.projects_query_service.get_project_by_id(project_id)
        if current is None:
            raise NotFoundError("Project not found")
        return current

    @router.get("/projects/<project_id>/management")
    def get_project_management(project_id: str):
        # 404 while the portal owns the project: the resource does not exist, so import finds nothing.
        access.require_project_access(
            router, dependencies.projects_query_service, project_id, "clients/projects/program.read"
        )
        current = existing_project(project_id)
        if not current.managedBy:
            raise NotFoundError("Project is not externally managed")
        return api_model.ProjectManagementResponse(
            projectId=project_id, managedBy=current.managedBy, source=current.managedSource or ""
        )

    @router.put("/projects/<project_id>/management")
    def put_project_management(project_id: str, request: api_model.ProjectManagement):
        # Upsert; repeating it with the same body changes nothing.
        access.require_project_access(
            router, dependencies.projects_query_service, project_id, "clients/projects/program.write"
        )
        try:
            dependencies.project_lifecycle_service.set_management(project_id, request.managedBy, request.source)
        except KeyError as exc:
            raise NotFoundError("Project not found") from exc
        return api_model.ProjectManagementResponse(
            projectId=project_id, managedBy=request.managedBy, source=request.source
        )

    @router.delete("/projects/<project_id>/management")
    def delete_project_management(project_id: str):
        # Hands the project back to the portal. Idempotent: a missing project or mark is already the
        # desired state.
        access.require_scope(router, "clients/projects/program.write")
        if dependencies.projects_query_service.get_project_by_id(project_id) is None:
            return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT)
        access.require_project_access(
            router, dependencies.projects_query_service, project_id, "clients/projects/program.write"
        )
        dependencies.project_lifecycle_service.set_management(project_id, None, None)
        return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT)

    def lifecycle_response(project_id: str, settings: workbench_lifecycle.WorkbenchLifecycle):
        return api_model.ProjectWorkbenchLifecycleResponse(projectId=project_id, **settings.model_dump())

    @router.get("/projects/<project_id>/workbench-lifecycle")
    def get_project_workbench_lifecycle(project_id: str):
        # 404 while the project uses the deployment's defaults, so import finds nothing.
        access.require_project_access(
            router, dependencies.projects_query_service, project_id, "clients/projects/program.read"
        )
        current = existing_project(project_id)
        if current.workbenchLifecycle is None:
            raise NotFoundError("Project uses the default workbench lifecycle")
        return lifecycle_response(project_id, current.workbenchLifecycle)

    @router.put("/projects/<project_id>/workbench-lifecycle")
    def put_project_workbench_lifecycle(project_id: str, request: workbench_lifecycle.WorkbenchLifecycle):
        # Upsert of the whole policy; omitted fields take their defaults.
        access.require_project_access(
            router, dependencies.projects_query_service, project_id, "clients/projects/program.write"
        )
        try:
            dependencies.project_lifecycle_service.set_workbench_lifecycle(project_id, request)
        except KeyError as exc:
            raise NotFoundError("Project not found") from exc
        return lifecycle_response(project_id, request)

    @router.delete("/projects/<project_id>/workbench-lifecycle")
    def delete_project_workbench_lifecycle(project_id: str):
        # Back to the deployment's defaults. Idempotent: a missing project or policy is the desired state.
        access.require_scope(router, "clients/projects/program.write")
        if dependencies.projects_query_service.get_project_by_id(project_id) is None:
            return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT)
        access.require_project_access(
            router, dependencies.projects_query_service, project_id, "clients/projects/program.write"
        )
        dependencies.project_lifecycle_service.set_workbench_lifecycle(project_id, None)
        return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT)

    return router
