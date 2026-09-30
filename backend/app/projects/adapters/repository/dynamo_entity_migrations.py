import typing
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key
from mypy_boto3_dynamodb import service_resource

from app.projects.adapters.repository import dynamo_entity_config
from app.shared.adapters.unit_of_work_v2 import dynamodb_repo_config, unit_of_work


def migrations_config(gsi_entities: str):

    def project_account_set_project_id(table: service_resource.Table):
        """001.ProjectAccount_Set_Project_ID

        Adds projectId attribute to all project account entities.
        """

        for project in __get_all_projects(table, gsi_entities):
            project_id = project.get("projectId")

            with table.batch_writer() as batch:
                for account in __get_all_project_accounts(table, project_id):
                    batch.put_item(
                        Item={
                            **account,
                            "projectId": project.get("projectId"),
                        }
                    )

    def project_and_account_set_sequence_id(table: service_resource.Table):
        """002.Project_and_Account_Set_Sequence_ID

        Adds sequenceId attribute to all project and account entities.
        This is to enable optimistic concurrency control on those entities.
        """

        for project in __get_all_projects(table, gsi_entities):
            project_id = project.get("projectId")

            with table.batch_writer() as batch:
                batch.put_item(
                    Item={
                        **project,
                        unit_of_work.ATTRIBUTE_NAME_SEQUENCE_NO: 0,
                    }
                )
                for account in __get_all_project_accounts(table, project_id):
                    batch.put_item(
                        Item={
                            **account,
                            unit_of_work.ATTRIBUTE_NAME_SEQUENCE_NO: 0,
                        }
                    )

    def project_account_set_onboarded_at(table: service_resource.Table):
        """003.ProjectAccount_Set_OnboardedAt

        Accounts onboarded before onboardedAt existed get it from their last successful state, so
        tooling gating on it sees them as onboarded. Only the one attribute is set, and only where it
        is missing, so a re-run changes nothing.
        """

        for project in __get_all_projects(table, gsi_entities):
            for account in __get_all_project_accounts(table, project.get("projectId")):
                if account.get("onboardedAt"):
                    continue
                onboarded = account.get("lastOnboardingResult") == "Succeeded" or account.get("accountStatus") in (
                    "Active",
                    "ReOnboarding",
                )
                if not onboarded:
                    continue
                when = (
                    account.get("lastUpdateDate")
                    or account.get("createDate")
                    or datetime.now(timezone.utc).isoformat()
                )
                try:
                    table.update_item(
                        Key={"PK": account["PK"], "SK": account["SK"]},
                        UpdateExpression="SET onboardedAt = :when",
                        ConditionExpression="attribute_exists(PK) AND attribute_not_exists(onboardedAt)",
                        ExpressionAttributeValues={":when": when},
                    )
                except table.meta.client.exceptions.ConditionalCheckFailedException:
                    pass

    return [
        (
            "001.ProjectAccount_Set_Project_ID",
            project_account_set_project_id,
        ),
        (
            "002.Project_and_Account_Set_Sequence_ID",
            project_and_account_set_sequence_id,
        ),
        (
            "003.ProjectAccount_Set_OnboardedAt",
            project_account_set_onboarded_at,
        ),
    ]


def __get_all_projects(table: service_resource.Table, gsi_entities: str) -> typing.Iterator[dict]:
    cli = table.meta.client
    paginator = cli.get_paginator("query")
    all_projects_paginator = paginator.paginate(
        TableName=table.table_name,
        IndexName=gsi_entities,
        KeyConditionExpression=Key(dynamodb_repo_config.ATTRIBUTE_NAME_ENTITY).eq(
            dynamo_entity_config.DBPrefix.PROJECT.value
        ),
    )

    for page in all_projects_paginator:
        for project in page.get("Items", []):
            yield project


def __get_all_project_accounts(table: service_resource.Table, project_id: str) -> typing.Iterator[dict]:
    cli = table.meta.client
    paginator = cli.get_paginator("query")

    pk = f"{dynamo_entity_config.DBPrefix.PROJECT.value}#{project_id}"
    sk = f"{dynamo_entity_config.DBPrefix.ACCOUNT.value}#"
    accounts_paginator = paginator.paginate(
        TableName=table.table_name,
        KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(sk),
    )
    for acc_page in accounts_paginator:
        for account in acc_page.get("Items", []):
            yield account
