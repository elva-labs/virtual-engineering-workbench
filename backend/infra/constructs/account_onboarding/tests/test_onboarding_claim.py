import aws_cdk
import boto3
import pytest
from aws_cdk import aws_dynamodb, aws_ec2, aws_ecs, aws_lambda
from botocore.exceptions import ClientError
from moto import mock_aws

from infra import config
from infra.constructs.account_onboarding.account_onboarding_state_machine import (
    CLAIM_CONDITION_EXPRESSION,
    CLAIM_EXPRESSION_ATTRIBUTE_NAMES,
    CLAIM_UPDATE_EXPRESSION,
    AccountOnboardingStateMachine,
)


def _claim_operation(table, operation_id: str):
    return table.update_item(
        Key={
            "PK": "PROJECT#project-123",
            "SK": "ACCOUNT#account-record-456",
        },
        UpdateExpression=CLAIM_UPDATE_EXPRESSION,
        ConditionExpression=CLAIM_CONDITION_EXPRESSION,
        ExpressionAttributeNames=CLAIM_EXPRESSION_ATTRIBUTE_NAMES,
        ExpressionAttributeValues={":operationId": operation_id},
        ReturnValues="ALL_NEW",
    )


def test_onboarding_claim_is_conditional_and_duplicate_claims_exit_before_setup():
    app = aws_cdk.App()
    stack = aws_cdk.Stack(app, "OnboardingClaimTest")
    app_config = config.AppConfig(
        account="111111111111",
        region="eu-west-1",
        environment="dev",
        web_app_account="111111111111",
        image_service_account="222222222222",
        catalog_service_account="333333333333",
        component_name="projects",
        environment_config=config.env_config["dev"],
        component_specific=config.projects_app_config["dev"],
    )
    vpc = aws_ec2.Vpc(stack, "Vpc", max_azs=1)
    cluster = aws_ecs.Cluster(stack, "Cluster", vpc=vpc)
    task_definition = aws_ecs.FargateTaskDefinition(stack, "TaskDefinition")
    task_definition.add_container(
        "Container",
        image=aws_ecs.ContainerImage.from_registry("public.ecr.aws/amazonlinux/amazonlinux:latest"),
    )
    handler = aws_lambda.Function(
        stack,
        "Handler",
        runtime=aws_lambda.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=aws_lambda.Code.from_inline("def handler(event, context): return event"),
    )
    table = aws_dynamodb.Table(
        stack,
        "ProjectsTable",
        partition_key=aws_dynamodb.Attribute(name="PK", type=aws_dynamodb.AttributeType.STRING),
        sort_key=aws_dynamodb.Attribute(name="SK", type=aws_dynamodb.AttributeType.STRING),
    )
    onboarding = AccountOnboardingStateMachine(
        stack,
        "AccountOnboarding",
        app_config=app_config,
        account_onboarding_lambda=handler,
        ecs_cluster=cluster,
        task_definition=task_definition,
        projects_table=table,
    )

    claim_state = onboarding.node.find_child("ClaimOnboardingOperation").to_state_json()
    assert claim_state["Resource"].endswith("aws-sdk:dynamodb:updateItem")
    assert claim_state["Parameters"]["ExpressionAttributeNames"]["#operationId"] == "onboardingOperationId"
    assert claim_state["Parameters"]["ExpressionAttributeNames"]["#claimedOperation"] == "onboardingClaimedOperationId"
    assert any(
        "ConditionalCheckFailedException" in error
        for catcher in claim_state["Catch"]
        for error in catcher["ErrorEquals"]
    )
    assert claim_state["Catch"][0]["Next"] == "DuplicateOnboardingOperation"
    assert onboarding.node.find_child("DuplicateOnboardingOperation").to_state_json()["Type"] == "Succeed"
    choice = onboarding.node.find_child("HasOnboardingOperationId").to_state_json()
    assert choice["Choices"][0]["Next"] == "ClaimOnboardingOperation"
    assert choice["Default"] == "SetupPrerequisitesResources"
    assert claim_state["Next"] == "SetupPrerequisitesResources"


@mock_aws
def test_dynamodb_claim_allows_one_attempt_and_next_operation_without_replacing_account():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.create_table(
        TableName="projects",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.put_item(
        Item={
            "PK": "PROJECT#project-123",
            "SK": "ACCOUNT#account-record-456",
            "id": "account-record-456",
            "projectId": "project-123",
            "awsAccountId": "123456789012",
            "onboardingOperationId": "operation-1",
            "accountStatus": "OnBoarding",
        }
    )
    identity_before = {
        key: table.get_item(Key={"PK": "PROJECT#project-123", "SK": "ACCOUNT#account-record-456"})["Item"][key]
        for key in ("PK", "SK", "id", "projectId", "awsAccountId")
    }

    first_claim = _claim_operation(table, "operation-1")
    assert first_claim["Attributes"]["onboardingClaimedOperationId"] == "operation-1"

    with pytest.raises(ClientError) as duplicate_running:
        _claim_operation(table, "operation-1")
    assert duplicate_running.value.response["Error"]["Code"] == "ConditionalCheckFailedException"

    table.update_item(
        Key={"PK": "PROJECT#project-123", "SK": "ACCOUNT#account-record-456"},
        UpdateExpression="SET accountStatus = :status, lastOnboardingResult = :result",
        ExpressionAttributeValues={":status": "Active", ":result": "Succeeded"},
    )
    with pytest.raises(ClientError) as duplicate_completed:
        _claim_operation(table, "operation-1")
    assert duplicate_completed.value.response["Error"]["Code"] == "ConditionalCheckFailedException"

    table.update_item(
        Key={"PK": "PROJECT#project-123", "SK": "ACCOUNT#account-record-456"},
        UpdateExpression="SET onboardingOperationId = :operationId, accountStatus = :status",
        ExpressionAttributeValues={
            ":operationId": "operation-2",
            ":status": "ReOnboarding",
        },
    )
    next_claim = _claim_operation(table, "operation-2")
    assert next_claim["Attributes"]["onboardingClaimedOperationId"] == "operation-2"

    final_item = table.get_item(Key={"PK": "PROJECT#project-123", "SK": "ACCOUNT#account-record-456"})["Item"]
    assert {key: final_item[key] for key in identity_before} == identity_before
