import logging
from unittest import mock

from app.publishing.adapters.query_services.projects_api_technologies_query_service import (
    ProjectsApiTechnologiesQueryService,
)
from app.publishing.adapters.services.projects_api_service_client_project_access_service import (
    ProjectsApiServiceClientProjectAccessService,
)
from app.publishing.adapters.services.provisioning_api_version_workbenches_query_service import (
    ProvisioningApiVersionWorkbenchesQueryService,
)
from app.publishing.domain.commands import (
    archive_product_command,
    create_product_command,
    promote_version_command,
    retire_version_command,
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
        retire_version_command.RetireVersionCommand,
    ):
        assert command.__name__ in registered_handlers
    assert isinstance(dependencies.project_access_service, ProjectsApiServiceClientProjectAccessService)
    assert isinstance(dependencies.technologies_query_service, ProjectsApiTechnologiesQueryService)
    assert isinstance(dependencies.version_workbenches_query_service, ProvisioningApiVersionWorkbenchesQueryService)
    # Projects for access and technologies; Provisioning for the workbenches on a version.
    assert [c.args[0] for c in registry.return_value.api_for.call_args_list] == [
        bootstrapper.bounded_contexts.BoundedContext.PROJECTS,
        bootstrapper.bounded_contexts.BoundedContext.PROVISIONING,
    ]
