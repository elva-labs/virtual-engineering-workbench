from datetime import datetime, timezone
from http import HTTPStatus

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types

from app.projects.domain.commands.technologies import (
    add_technology,
    delete_technology_command,
    update_technology_command,
)
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import technology as technology_model
from app.projects.domain.value_objects import project_id_value_object, tech_id_value_object
from app.projects.entrypoints.s2s_api import bootstrapper, common, idempotency, s2s_exception
from app.projects.entrypoints.s2s_api.model import api_model

tracer = Tracer()

READ_SCOPE = "clients/projects/technology.read"
WRITE_SCOPE = "clients/projects/technology.write"


def _technology_response(
    technology: technology_model.Technology,
) -> api_model.Technology:
    return api_model.Technology(
        technologyId=technology.id,
        projectId=technology.project_id,
        name=technology.name,
        description=technology.description,
        createDate=technology.createDate,
        lastUpdateDate=technology.lastUpdateDate,
    )


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()

    def technology_in_project(project_id: str, technology_id: str) -> technology_model.Technology:
        technology = dependencies.technologies_query_service.get_technology_by_id(project_id, technology_id)
        if technology is None or technology.project_id != project_id:
            raise s2s_exception.ResourceNotFound()
        return technology

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/technologies")
    def list_technologies(project_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        technologies = dependencies.technologies_query_service.list_technologies(project_id, page_size=1000)
        return api_model.TechnologyPage(technologies=[_technology_response(item) for item in technologies])

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.post("/projects/<project_id>/technologies")
    def create_technology(project_id: str, request: api_model.CreateTechnologyRequest):
        client = common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        scope = common.idempotency_scope(router, client, project_id, "CREATE_TECHNOLOGY")

        def create(technology_id: str) -> idempotency.StoredCreateResponse:
            dependencies.command_bus.handle(
                add_technology.AddTechnologyCommand(
                    name=request.name,
                    description=request.description,
                    project_id=project_id_value_object.from_str(project_id),
                    technology_id=technology_id,
                )
            )
            return idempotency.StoredCreateResponse(HTTPStatus.CREATED, {"technologyId": technology_id})

        result = idempotency.execute_create(
            service=dependencies.idempotency_service,
            scope=scope,
            request=request,
            resource_id=technology_model.uuid_to_str(),
            resource_exists=lambda resource_id: dependencies.technologies_query_service.get_technology_by_id(
                project_id, resource_id
            )
            is not None,
            response_for_id=lambda resource_id: idempotency.StoredCreateResponse(
                HTTPStatus.CREATED, {"technologyId": resource_id}
            ),
            create=create,
            now=datetime.now(timezone.utc),
        )
        return api_gateway.Response(
            status_code=result.status_code,
            body=api_model.CreateTechnologyResponse.model_validate(result.body),
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/technologies/<technology_id>")
    def get_technology(project_id: str, technology_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        return _technology_response(technology_in_project(project_id, technology_id))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put("/projects/<project_id>/technologies/<technology_id>")
    def update_technology(project_id: str, technology_id: str, request: api_model.UpdateTechnologyRequest):
        common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        technology_in_project(project_id, technology_id)
        dependencies.command_bus.handle(
            update_technology_command.UpdateTechnologyCommand(
                id=tech_id_value_object.from_str(technology_id),
                project_id=project_id_value_object.from_str(project_id),
                name=request.name,
                description=request.description,
            )
        )
        return _technology_response(technology_in_project(project_id, technology_id))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.delete("/projects/<project_id>/technologies/<technology_id>")
    def delete_technology(project_id: str, technology_id: str):
        common.authorize(router, dependencies, project_id, WRITE_SCOPE)
        existing = dependencies.technologies_query_service.get_technology_by_id(project_id, technology_id)
        if existing is None:
            return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT, headers=common.NO_STORE)
        if existing.project_id != project_id:
            raise s2s_exception.ResourceNotFound()
        try:
            dependencies.command_bus.handle(
                delete_technology_command.DeleteTechnologyCommand(
                    id=tech_id_value_object.from_str(technology_id),
                    project_id=project_id_value_object.from_str(project_id),
                )
            )
        except domain_exception.TechnologyInUseException as error:
            raise s2s_exception.TechnologyInUseConflict() from error
        return api_gateway.Response(
            status_code=HTTPStatus.NO_CONTENT,
            headers=common.NO_STORE,
            content_type=content_types.APPLICATION_JSON,
        )

    return router
