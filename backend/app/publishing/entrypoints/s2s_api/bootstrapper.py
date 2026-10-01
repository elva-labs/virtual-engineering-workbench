from functools import partial
from typing import Optional

import boto3
from aws_lambda_powertools import logging
from pydantic import BaseModel, ConfigDict

from app.publishing.adapters.query_services import (
    dynamodb_amis_query_service,
    dynamodb_portfolios_query_service,
    dynamodb_products_query_service,
    dynamodb_versions_query_service,
    projects_api_technologies_query_service,
)
from app.publishing.adapters.repository import dynamo_entity_config
from app.publishing.adapters.services.projects_api_service_client_project_access_service import (
    ProjectsApiServiceClientProjectAccessService,
)
from app.publishing.domain.command_handlers import (
    archive_product_command_handler,
    create_product_command_handler,
    promote_version_command_handler,
    update_product_command_handler,
)
from app.publishing.domain.commands import (
    archive_product_command,
    create_product_command,
    promote_version_command,
    update_product_command,
)
from app.publishing.domain.model import product
from app.publishing.domain.ports.service_client_project_access_service import ServiceClientProjectAccessService
from app.publishing.domain.ports.technologies_query_service import TechnologiesQueryService
from app.publishing.entrypoints.s2s_api import config
from app.shared.adapters.idempotency import dynamodb_idempotency_service
from app.shared.adapters.message_bus import (
    command_bus,
    command_bus_metrics,
    event_bridge_message_bus,
    in_memory_command_bus,
    message_bus_metrics,
)
from app.shared.adapters.unit_of_work_v2 import dynamodb_unit_of_work, unit_of_work
from app.shared.api import aws_events_api, bounded_contexts, service_registry
from app.shared.domain.ports.idempotency_service import IdempotencyService
from app.shared.instrumentation import power_tools_metrics
from app.shared.logging import boto_logger


class ProductsReader:
    """Products of a project, with a missing product as None (the S2S API answers 404, not 500)."""

    def __init__(
        self,
        uow: unit_of_work.UnitOfWork,
        products_qry_srv: dynamodb_products_query_service.DynamoDBProductsQueryService,
    ) -> None:
        self._uow = uow
        self._products_qry_srv = products_qry_srv

    def get_product(self, project_id: str, product_id: str) -> Optional[product.Product]:
        with self._uow:
            return self._uow.get_repository(product.ProductPrimaryKey, product.Product).get(
                pk=product.ProductPrimaryKey(projectId=project_id, productId=product_id)
            )

    def get_products(self, project_id: str) -> list[product.Product]:
        return self._products_qry_srv.get_products(project_id=project_id)


class Dependencies(BaseModel):
    command_bus: command_bus.CommandBus
    products_query_service: ProductsReader
    versions_query_service: dynamodb_versions_query_service.DynamoDBVersionsQueryService
    project_access_service: ServiceClientProjectAccessService
    technologies_query_service: TechnologiesQueryService
    idempotency_service: IdempotencyService
    platform_program_id: str = ""
    model_config = ConfigDict(arbitrary_types_allowed=True)


def bootstrap(app_config: config.AppConfig, logger: logging.Logger) -> Dependencies:
    session = boto_logger.loggable_session(boto3.session.Session(), logger)
    region = app_config.get_default_region()
    dynamodb_client = session.resource("dynamodb", region_name=region).meta.client
    table_name = app_config.get_table_name()

    uow = dynamodb_unit_of_work.DynamoDBUnitOfWork(
        table_name=table_name,
        dynamodb_client=dynamodb_client,
        repo_factories=dynamo_entity_config.EntityConfigurator(table_name=table_name).repo_factories(),
        logger=logger,
    )
    metrics_client = power_tools_metrics.PowerToolsMetrics()
    message_bus = message_bus_metrics.MessageBusMetrics(
        inner=event_bridge_message_bus.EventBridgeMessageBus(
            events_api=aws_events_api.AWSEventsApi(client=session.client("events", region_name=region)),
            event_bus_name=app_config.get_domain_event_bus_name(),
            bounded_context_name=app_config.get_bounded_context_name(),
            logger=logger,
        ),
        metrics_client=metrics_client,
        logger=logger,
    )

    gsi_name_entities = app_config.get_gsi_name_entities()
    versions_qry_srv = dynamodb_versions_query_service.DynamoDBVersionsQueryService(
        table_name=table_name, dynamodb_client=dynamodb_client, gsi_name_entities=gsi_name_entities
    )

    bus = (
        command_bus_metrics.CommandBusMetrics(
            inner=in_memory_command_bus.InMemoryCommandBus(logger=logger),
            metrics_client=metrics_client,
        )
        .register_handler(
            create_product_command.CreateProductCommand,
            partial(create_product_command_handler.handle, unit_of_work=uow, message_bus=message_bus),
        )
        .register_handler(
            update_product_command.UpdateProductCommand,
            partial(update_product_command_handler.handle, unit_of_work=uow),
        )
        .register_handler(
            archive_product_command.ArchiveProductCommand,
            partial(archive_product_command_handler.handle, uow=uow, message_bus=message_bus),
        )
        .register_handler(
            promote_version_command.PromoteVersionCommand,
            partial(
                promote_version_command_handler.handle,
                uow=uow,
                message_bus=message_bus,
                portf_qry_srv=dynamodb_portfolios_query_service.DynamoDBPortfoliosQueryService(
                    table_name=table_name, dynamodb_client=dynamodb_client, gsi_name_entities=gsi_name_entities
                ),
                versions_qry_srv=versions_qry_srv,
                amis_qry_srv=dynamodb_amis_query_service.DynamoDBAMIsQueryService(
                    table_name=table_name, dynamodb_client=dynamodb_client, gsi_name_entities=gsi_name_entities
                ),
            ),
        )
    )

    projects_api = service_registry.ServiceRegistry.from_config(
        app_config=app_config,
        ssm_client=session.client("ssm", region_name=region),
        logger=logger,
    ).api_for(bounded_contexts.BoundedContext.PROJECTS)

    return Dependencies(
        command_bus=bus,
        products_query_service=ProductsReader(
            uow,
            dynamodb_products_query_service.DynamoDBProductsQueryService(
                table_name=table_name,
                dynamodb_client=dynamodb_client,
                gsi_name_entities=gsi_name_entities,
            ),
        ),
        versions_query_service=versions_qry_srv,
        project_access_service=ProjectsApiServiceClientProjectAccessService(api=projects_api),
        technologies_query_service=projects_api_technologies_query_service.ProjectsApiTechnologiesQueryService(
            api=projects_api
        ),
        # Records live in the Publishing table (TTL on ExpireDate).
        idempotency_service=dynamodb_idempotency_service.DynamoDBIdempotencyService(table_name, dynamodb_client),
        platform_program_id=app_config.get_platform_program_id(),
    )
