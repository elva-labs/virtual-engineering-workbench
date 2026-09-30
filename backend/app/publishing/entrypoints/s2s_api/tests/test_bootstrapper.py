import logging
from unittest import mock

from app.publishing.adapters.query_services.projects_api_technologies_query_service import (
    ProjectsApiTechnologiesQueryService,
)
from app.publishing.adapters.services.projects_api_service_client_project_access_service import (
    ProjectsApiServiceClientProjectAccessService,
)
from app.publishing.domain.commands import (
    archive_product_command,
    create_product_command,
    promote_version_command,
    update_product_command,
)
from app.publishing.entrypoints.s2s_api import bootstrapper, config


def test_bootstrapper_registers_the_s2s_commands_and_the_projects_adapters(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "TEST")
    monkeypatch.setenv("GSI_NAME_ENTITIES", "entities")
    app_config = config.AppConfig(**config.config)

    with mock.patch.object(bootstrapper.service_registry.ServiceRegistry, "from_config") as registry:
        dependencies = bootstrapper.bootstrap(app_config=app_config, logger=logging.getLogger())

    registered_handlers = dependencies.command_bus._inner._command_handlers
    for command in (
        create_product_command.CreateProductCommand,
        update_product_command.UpdateProductCommand,
        archive_product_command.ArchiveProductCommand,
        promote_version_command.PromoteVersionCommand,
    ):
        assert command.__name__ in registered_handlers
    assert isinstance(dependencies.project_access_service, ProjectsApiServiceClientProjectAccessService)
    assert isinstance(dependencies.technologies_query_service, ProjectsApiTechnologiesQueryService)
    registry.return_value.api_for.assert_called_once_with(bootstrapper.bounded_contexts.BoundedContext.PROJECTS)
