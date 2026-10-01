import boto3
from aws_lambda_powertools import logging
from pydantic import BaseModel, ConfigDict

from app.publishing.adapters.query_services import (
    dynamodb_products_query_service,
    dynamodb_versions_query_service,
    service_catalog_query_service,
)
from app.publishing.adapters.repository import dynamo_entity_config
from app.publishing.adapters.repository.dynamo_entity_migrations import migrations_config
from app.publishing.adapters.services import service_catalog_service
from app.publishing.domain.command_handlers import (
    create_portfolio_command_handler,
    distribute_platform_versions_command_handler,
)
from app.publishing.domain.commands import create_portfolio_command, distribute_platform_versions_command
from app.publishing.entrypoints.projects_event_handler import config
from app.shared.adapters.message_bus import (
    command_bus,
    command_bus_metrics,
    event_bridge_message_bus,
    in_memory_command_bus,
    message_bus_metrics,
)
from app.shared.adapters.unit_of_work_v2 import dynamodb_migrations, dynamodb_unit_of_work
from app.shared.api import aws_events_api
from app.shared.instrumentation import power_tools_metrics
from app.shared.logging import boto_logger


class Dependencies(BaseModel):
    command_bus: command_bus.CommandBus
    model_config = ConfigDict(arbitrary_types_allowed=True)


def bootstrap(
    app_config: config.AppConfig,
    logger: logging.Logger,
) -> Dependencies:
    session = boto_logger.loggable_session(boto3.session.Session(), logger)
    dynamodb = session.resource("dynamodb", region_name=app_config.get_default_region())

    # Portfolios are written only here, so their key migration (ADR 0013) runs here too.
    dynamodb_migrations.DynamoDBMigrator(
        ddb_resource=dynamodb,
        table_name=app_config.get_table_name(),
        logger=logger,
    ).register_migrations(migrations_config()).migrate()

    shared_uow = dynamodb_unit_of_work.DynamoDBUnitOfWork(
        table_name=app_config.get_table_name(),
        dynamodb_client=dynamodb.meta.client,
        repo_factories=dynamo_entity_config.EntityConfigurator(table_name=app_config.get_table_name()).repo_factories(),
        logger=logger,
    )
    sc_service = service_catalog_service.ServiceCatalogService(
        admin_role=app_config.get_admin_role(),
        use_case_role=app_config.get_use_case_role(),
        launch_constraint_role=app_config.get_launch_constraint_role(),
        notification_constraint_topic_arn_resolver=app_config.get_notification_arn_for_region,
        tools_aws_account_id=app_config.get_tools_aws_account_id(),
        bucket_name=app_config.get_templates_s3_bucket_name(),
        boto_session=session,
        resource_update_constraint_allowed=app_config.get_resource_update_constraint_allowed_value(),
    )

    sc_qry_service = service_catalog_query_service.ServiceCatalogQueryService(
        admin_role=app_config.get_admin_role(),
        use_case_role=app_config.get_use_case_role(),
        technical_parameters_names=app_config.get_technical_parameters_names(),
        tools_aws_account_id=app_config.get_tools_aws_account_id(),
        logger=logger,
        boto_session=session,
    )

    def _create_portfolio_handler_factory():
        def _handle_command(
            command: create_portfolio_command.CreatePortfolioCommand,
        ):
            return create_portfolio_command_handler.handle(
                cmd=command,
                uow=shared_uow,
                catalog_qry_srv=sc_qry_service,
                catalog_srv=sc_service,
                logger=logger,
                main_account_roles={app_config.get_admin_role()},
                spoke_account_roles={app_config.get_provisioning_role()},
            )

        return _handle_command

    metrics_client = power_tools_metrics.PowerToolsMetrics()
    domain_message_bus = message_bus_metrics.MessageBusMetrics(
        inner=event_bridge_message_bus.EventBridgeMessageBus(
            events_api=aws_events_api.AWSEventsApi(
                client=session.client("events", region_name=app_config.get_default_region())
            ),
            event_bus_name=app_config.get_domain_event_bus_name(),
            bounded_context_name=app_config.get_bounded_context_name(),
            logger=logger,
        ),
        metrics_client=metrics_client,
        logger=logger,
    )
    products_qry_srv = dynamodb_products_query_service.DynamoDBProductsQueryService(
        table_name=app_config.get_table_name(),
        dynamodb_client=dynamodb.meta.client,
        gsi_name_entities=app_config.get_gsi_name_entities(),
    )
    versions_qry_srv = dynamodb_versions_query_service.DynamoDBVersionsQueryService(
        table_name=app_config.get_table_name(),
        dynamodb_client=dynamodb.meta.client,
        gsi_name_entities=app_config.get_gsi_name_entities(),
    )

    def _distribute_platform_versions_handler(
        command: distribute_platform_versions_command.DistributePlatformVersionsCommand,
    ):
        return distribute_platform_versions_command_handler.handle(
            cmd=command,
            uow=shared_uow,
            message_bus=domain_message_bus,
            products_qry_srv=products_qry_srv,
            versions_qry_srv=versions_qry_srv,
            platform_program_id=app_config.get_platform_program_id(),
            logger=logger,
        )

    command_bus = (
        command_bus_metrics.CommandBusMetrics(
            inner=in_memory_command_bus.InMemoryCommandBus(logger=logger),
            metrics_client=metrics_client,
        )
        .register_handler(
            create_portfolio_command.CreatePortfolioCommand,
            _create_portfolio_handler_factory(),
        )
        .register_handler(
            distribute_platform_versions_command.DistributePlatformVersionsCommand,
            _distribute_platform_versions_handler,
        )
    )

    return Dependencies(
        command_bus=command_bus,
    )
