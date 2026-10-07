from http import HTTPStatus
from uuid import UUID

from aws_lambda_powertools.event_handler import api_gateway, content_types
from aws_lambda_powertools.event_handler.api_gateway import Router
from aws_lambda_powertools.event_handler.exceptions import BadRequestError, NotFoundError

from app.projects.entrypoints.s2s_api import bootstrapper
from app.projects.entrypoints.s2s_api.model import api_model
from app.projects.entrypoints.s2s_api.routers import access


def _group_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise BadRequestError("Invalid Entra group ID") from exc


def init(dependencies: bootstrapper.Dependencies) -> Router:
    router = Router()

    @router.get("/projects/<project_id>/groups")
    def list_groups(project_id: str):
        access.require_project_access(
            router,
            dependencies.projects_query_service,
            project_id,
            "clients/projects/group_assignment.read",
        )
        assignments = dependencies.projects_query_service.list_project_group_assignments(project_id)
        return api_model.GetProjectGroupAssignmentsResponse(
            assignments=[api_model.ProjectGroupAssignment.model_validate(a.model_dump()) for a in assignments]
        )

    @router.get("/projects/<project_id>/groups/<group_id>")
    def get_group(project_id: str, group_id: str):
        access.require_project_access(
            router,
            dependencies.projects_query_service,
            project_id,
            "clients/projects/group_assignment.read",
        )
        assignment = dependencies.projects_query_service.get_project_group_assignment(project_id, _group_id(group_id))
        if assignment is None or assignment.isDeleted:
            raise NotFoundError("Group assignment not found")
        return api_model.ProjectGroupAssignment.model_validate(assignment.model_dump())

    @router.put("/projects/<project_id>/groups/<group_id>")
    def put_group(
        request: api_model.PutProjectGroupAssignmentRequest,
        project_id: str,
        group_id: str,
    ):
        access.require_project_access(
            router,
            dependencies.projects_query_service,
            project_id,
            "clients/projects/group_assignment.write",
        )
        try:
            assignment = dependencies.group_assignment_service.put(
                project_id, _group_id(group_id), request.roles, request.groupName
            )
        except ValueError as exc:
            raise BadRequestError("Invalid group assignment") from exc
        return api_model.ProjectGroupAssignment.model_validate(assignment.model_dump())

    @router.delete("/projects/<project_id>/groups/<group_id>")
    def delete_group(project_id: str, group_id: str):
        access.require_project_access(
            router,
            dependencies.projects_query_service,
            project_id,
            "clients/projects/group_assignment.write",
        )
        dependencies.group_assignment_service.delete(project_id, _group_id(group_id))
        return api_gateway.Response(
            status_code=HTTPStatus.NO_CONTENT,
            body="",
            content_type=content_types.APPLICATION_JSON,
        )

    return router
