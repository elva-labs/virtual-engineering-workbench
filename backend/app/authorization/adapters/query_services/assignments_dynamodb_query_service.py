from boto3.dynamodb.conditions import Key
from mypy_boto3_dynamodb import client

from app.authorization.adapters.repository import dynamo_entity_config
from app.authorization.domain.ports import assignments_query_service
from app.authorization.domain.read_models import (
    project_assignment,
    project_group_assignment,
    project_settings,
)


class AssignmentsDynamoDBQueryService(assignments_query_service.AssignmentsQueryService):

    def __init__(
        self,
        table_name: str,
        dynamodb_client: client.DynamoDBClient,
        gsi_inverted_pk: str,
    ):
        super().__init__()
        self.__table_name = table_name
        self.__dynamodb_client = dynamodb_client
        self.__gsi_inverted_pk = gsi_inverted_pk

    def get_user_assignments(self, user_id: str) -> list[project_assignment.Assignment]:

        paginator = self.__dynamodb_client.get_paginator("query")
        ret_val: list[project_assignment.Assignment] = []

        pages = paginator.paginate(
            TableName=self.__table_name,
            KeyConditionExpression=Key("PK").eq(f"{dynamo_entity_config.DBPrefix.USER.value}#{user_id}")
            & Key("SK").begins_with(f"{dynamo_entity_config.DBPrefix.PROJECT.value}#"),
        )
        for page in pages:
            ret_val.extend([project_assignment.Assignment.model_validate(item) for item in page["Items"]])

        return ret_val

    def get_project_settings(self, project_id: str) -> project_settings.ProjectSettings:
        response = self.__dynamodb_client.get_item(
            TableName=self.__table_name,
            Key={
                "PK": f"{dynamo_entity_config.DBPrefix.PROJECT.value}#{project_id}",
                "SK": f"{dynamo_entity_config.DBPrefix.SETTINGS.value}#{project_id}",
            },
        )
        if item := response.get("Item"):
            return project_settings.ProjectSettings.model_validate(item)
        return project_settings.ProjectSettings(projectId=project_id)

    def get_project_assignments(self, project_id: str) -> list[project_assignment.Assignment]:
        paginator = self.__dynamodb_client.get_paginator("query")
        ret_val: list[project_assignment.Assignment] = []

        pages = paginator.paginate(
            TableName=self.__table_name,
            KeyConditionExpression=Key("SK").eq(f"{dynamo_entity_config.DBPrefix.PROJECT.value}#{project_id}")
            & Key("PK").begins_with(f"{dynamo_entity_config.DBPrefix.USER.value}#"),
            IndexName=self.__gsi_inverted_pk,
        )
        for page in pages:
            ret_val.extend([project_assignment.Assignment.model_validate(item) for item in page["Items"]])

        return ret_val

    def get_group_assignments(self, group_ids: list[str]) -> list[project_group_assignment.GroupAssignment]:
        paginator = self.__dynamodb_client.get_paginator("query")
        assignments = []
        for group_id in sorted(set(group_ids)):
            for page in paginator.paginate(
                TableName=self.__table_name,
                KeyConditionExpression=Key("PK").eq(f"GROUP#{group_id}") & Key("SK").begins_with("PROJECT#"),
                ConsistentRead=True,
            ):
                assignments.extend(
                    project_group_assignment.GroupAssignment.model_validate(item)
                    for item in page.get("Items", [])
                    if not item.get("isDeleted", False)
                )
        return assignments
