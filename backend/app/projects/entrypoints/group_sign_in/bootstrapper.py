import os
from dataclasses import dataclass

import boto3

from app.projects.adapters.query_services.dynamodb_query_service import (
    DynamoDBEnrolmentQueryService,
    DynamoDBProjectsQueryService,
)
from app.projects.adapters.repository.dynamo_entity_config import EntityConfigurator
from app.projects.adapters.services.cognito_user_directory_service import CognitoUserDirectoryService
from app.projects.domain.command_handlers.users import (
    assign_user_command_handler,
    reassign_user_command_handler,
    unassign_user_command_handler,
)
from app.projects.domain.commands.users import assign_user_command, reassign_user_command, unassign_user_command
from app.projects.domain.group_membership_service import GroupMembershipService
from app.shared.adapters.message_bus.event_bridge_message_bus import EventBridgeMessageBus
from app.shared.adapters.message_bus.in_memory_command_bus import InMemoryCommandBus
from app.shared.adapters.unit_of_work_v2.dynamodb_unit_of_work import DynamoDBUnitOfWork
from app.shared.api.aws_events_api import AWSEventsApi
from app.shared.logging import boto_logger


@dataclass
class Dependencies:
    group_membership_service: GroupMembershipService


def bootstrap(logger) -> Dependencies:
    session = boto_logger.loggable_session(boto3.session.Session(), logger)
    dynamodb = session.resource("dynamodb", region_name=os.environ["AWS_DEFAULT_REGION"])
    table_name = os.environ["TABLE_NAME"]
    query = DynamoDBProjectsQueryService(
        table_name=table_name,
        dynamodb_client=dynamodb.meta.client,
        gsi_inverted_primary_key=os.environ["GSI_NAME_INVERTED_PK"],
        gsi_aws_accounts=os.environ["GSI_NAME_AWS_ACCOUNTS"],
        gsi_entities=os.environ["GSI_NAME_ENTITIES"],
    )
    enrolments = DynamoDBEnrolmentQueryService(
        table_name=table_name,
        dynamodb_client=dynamodb.meta.client,
        gsi_qpk=os.environ["GSI_NAME_QPK"],
        gsi_qsk=os.environ["GSI_NAME_QSK"],
    )
    uow = DynamoDBUnitOfWork(
        table_name=table_name,
        dynamodb_client=dynamodb.meta.client,
        repo_factories=EntityConfigurator(table_name).repo_factories(),
        logger=logger,
    )
    directory = CognitoUserDirectoryService(
        cognito_client=session.client("cognito-idp", region_name=os.environ["AWS_DEFAULT_REGION"]),
        user_pool_id=os.environ["COGNITO_USER_POOL_ID"],
        logger=logger,
    )
    events = EventBridgeMessageBus(
        events_api=AWSEventsApi(client=session.client("events", region_name=os.environ["AWS_DEFAULT_REGION"])),
        event_bus_name=os.environ["DOMAIN_EVENT_BUS_ARN"],
        bounded_context_name=os.environ["BOUNDED_CONTEXT"],
        logger=logger,
    )
    commands = InMemoryCommandBus(logger)
    commands.register_handler(
        assign_user_command.AssignUserCommand,
        lambda cmd: assign_user_command_handler.handle_assign_user_command(cmd, uow, query, events, directory),
    )
    commands.register_handler(
        reassign_user_command.ReAssignUserCommand,
        lambda cmd: reassign_user_command_handler.handle_reassign_user_command(cmd, uow, query, events),
    )
    commands.register_handler(
        unassign_user_command.UnAssignUserCommand,
        lambda cmd: unassign_user_command_handler.handle_unassign_user_command(cmd, uow, query, enrolments, events),
    )
    return Dependencies(GroupMembershipService(query, commands))
