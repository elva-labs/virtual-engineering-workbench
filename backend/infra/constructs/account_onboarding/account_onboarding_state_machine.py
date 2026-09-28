import aws_cdk
import cdk_nag
import constructs
from aws_cdk import Duration, aws_dynamodb, aws_ec2, aws_ecs, aws_iam, aws_lambda, aws_logs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as sfn_tasks

from infra import config

CLAIM_UPDATE_EXPRESSION = "SET #claimedOperation = :operationId"
CLAIM_CONDITION_EXPRESSION = (
    "attribute_exists(PK) AND #operationId = :operationId AND "
    "(attribute_not_exists(#claimedOperation) OR #claimedOperation <> :operationId)"
)
CLAIM_EXPRESSION_ATTRIBUTE_NAMES = {
    "#operationId": "onboardingOperationId",
    "#claimedOperation": "onboardingClaimedOperationId",
}


class AccountOnboardingStateMachine(constructs.Construct):
    def __init__(
        self,
        scope: constructs.Construct,
        id: str,
        app_config: config.AppConfig,
        account_onboarding_lambda: aws_lambda.Function,
        ecs_cluster: aws_ecs.Cluster,
        task_definition: aws_ecs.TaskDefinition,
        projects_table: aws_dynamodb.ITable,
    ) -> None:
        """
        Sample expected input for the state machine:
        {
            "version": "0",
            "id": "a05c801c-4d49-6552-5928-8620bb0207af",
            "detail-type": "accountonboarding-request",
            "source": "workbench.projects.dev",
            "account": "123456789012",
            "time": "2024-10-17T10:00:17Z",
            "region": "us-east-1",
            "resources": [],
            "detail": {
                "eventName": "accountonboarding-request",
                "programAccountId": "8017de89-19f7-4242-9f7c-abcdef123456",
                "accountId": "123456789012",
                "accountType": "workbench-user",
                "programName": "Test",
                "programId": "cd68168f-bae3-4840-a336-abcdef123456",
                "accountEnvironment": "dev",
                "region": "us-east-1"
            }
        }
        """
        super().__init__(scope, id)

        # Start State
        start: sfn.Pass = sfn.Pass(
            self,
            "Start",
            output_path="$.detail",
            assign={
                "projectId": sfn.JsonPath.string_at("$.detail.programId"),
                "projectAccountId": sfn.JsonPath.string_at("$.detail.programAccountId"),
            },
        )

        # Fail State
        fail: sfn.Fail = sfn.Fail(self, "Fail")

        # Success State
        success: sfn.Succeed = sfn.Succeed(self, "Success")

        already_claimed: sfn.Succeed = sfn.Succeed(self, "DuplicateOnboardingOperation")

        # The account row is the durable compare-and-set record for each attempt. A
        # single UpdateItem can claim an operation, so concurrent deliveries race
        # safely and only the winner reaches the resource-provisioning tasks.
        claim_operation = sfn_tasks.CallAwsService(
            self,
            "ClaimOnboardingOperation",
            service="dynamodb",
            action="updateItem",
            parameters={
                "TableName": projects_table.table_name,
                "Key": {
                    "PK": {
                        "S": sfn.JsonPath.format("PROJECT#{}", sfn.JsonPath.string_at("$.programId")),
                    },
                    "SK": {
                        "S": sfn.JsonPath.format("ACCOUNT#{}", sfn.JsonPath.string_at("$.programAccountId")),
                    },
                },
                "UpdateExpression": CLAIM_UPDATE_EXPRESSION,
                "ConditionExpression": CLAIM_CONDITION_EXPRESSION,
                "ExpressionAttributeNames": CLAIM_EXPRESSION_ATTRIBUTE_NAMES,
                "ExpressionAttributeValues": {
                    ":operationId": {"S.$": "$.onboardingOperationId"},
                },
            },
            iam_resources=[projects_table.table_arn],
            result_path=sfn.JsonPath.DISCARD,
        )
        claim_operation.add_catch(
            already_claimed,
            errors=[
                "DynamoDB.ConditionalCheckFailedException",
                "DynamoDb.ConditionalCheckFailedException",
            ],
            result_path=sfn.JsonPath.DISCARD,
        )

        # Setup prerequisites resources task
        setup_prerequisites_resources: sfn_tasks.EcsRunTask = sfn_tasks.EcsRunTask(
            self,
            "SetupPrerequisitesResources",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            cluster=ecs_cluster,
            task_definition=task_definition,
            launch_target=sfn_tasks.EcsFargateLaunchTarget(platform_version=aws_ecs.FargatePlatformVersion.LATEST),
            container_overrides=[
                sfn_tasks.ContainerOverride(
                    command=[
                        "/bin/sh",
                        "-c",
                        "python -m app.projects.entrypoints.account_onboarding.handler",
                    ],
                    container_definition=task_definition.default_container,
                    environment=[
                        {
                            "name": "AWS_ACCOUNT_ID",
                            "value": sfn.JsonPath.string_at("$.accountId"),
                        },
                        {"name": "REGION", "value": sfn.JsonPath.string_at("$.region")},
                        {
                            "name": "EVENT",
                            "value": sfn.JsonPath.string_at(
                                "States.JsonToString(States.JsonMerge($, States.StringToJson(States.Format("
                                "'\\{\"{}\": \"{}\"\\}', 'eventType', 'SetupPrerequisitesResourcesRequest')), false))"
                            ),
                        },
                        {
                            "name": "TASK_TOKEN",
                            "value": sfn.JsonPath.task_token,
                        },
                    ],
                )
            ],
            subnets=aws_ec2.SubnetSelection(subnets=ecs_cluster.vpc.private_subnets),
            task_timeout=sfn.Timeout.duration(Duration.hours(1)),
            result_path=sfn.JsonPath.DISCARD,
        )

        # Setup dynamic resources task
        setup_dynamic_resources: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "SetupDynamicResources",
            lambda_function=account_onboarding_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "SetupDynamicResourcesRequest",
                    "accountId.$": "$.accountId",
                    "region.$": "$.region",
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
        )

        # Setup static resources task
        setup_static_resources: sfn_tasks.EcsRunTask = sfn_tasks.EcsRunTask(
            self,
            "SetupStaticResources",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            cluster=ecs_cluster,
            task_definition=task_definition,
            launch_target=sfn_tasks.EcsFargateLaunchTarget(platform_version=aws_ecs.FargatePlatformVersion.LATEST),
            container_overrides=[
                sfn_tasks.ContainerOverride(
                    command=[
                        "/bin/sh",
                        "-c",
                        "python -m app.projects.entrypoints.account_onboarding.handler",
                    ],
                    container_definition=task_definition.default_container,
                    environment=[
                        {
                            "name": "AWS_ACCOUNT_ID",
                            "value": sfn.JsonPath.string_at("$.accountId"),
                        },
                        {"name": "REGION", "value": sfn.JsonPath.string_at("$.region")},
                        {
                            "name": "EVENT",
                            "value": sfn.JsonPath.string_at(
                                "States.JsonToString(States.JsonMerge($, States.StringToJson(States.Format("
                                "'\\{\"{}\": \"{}\"\\}', 'eventType', 'SetupStaticResourcesRequest')), false))"
                            ),
                        },
                        {
                            "name": "TASK_TOKEN",
                            "value": sfn.JsonPath.task_token,
                        },
                    ],
                )
            ],
            subnets=aws_ec2.SubnetSelection(subnets=ecs_cluster.vpc.private_subnets),
            task_timeout=sfn.Timeout.duration(Duration.hours(1)),
            result_path=sfn.JsonPath.DISCARD,
        )

        # Successful completion
        complete_onboarding: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "CompleteProjectAccountOnboarding",
            lambda_function=account_onboarding_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "CompleteProjectAccountOnboardingRequest",
                    "projectId.$": "$projectId",
                    "projectAccountId.$": "$projectAccountId",
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
        )

        # Failure handler
        fail_onboarding: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "FailProjectAccountOnboarding",
            lambda_function=account_onboarding_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "FailProjectAccountOnboardingRequest",
                    "projectId.$": "$projectId",
                    "projectAccountId.$": "$projectAccountId",
                    "error.$": "$.Error",
                    "cause.$": "$.Cause",
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
        )
        fail_onboarding.add_catch(fail)
        fail_onboarding.next(fail)
        complete_onboarding.add_catch(fail_onboarding)
        setup_dynamic_resources.add_catch(fail_onboarding)
        setup_prerequisites_resources.add_catch(fail_onboarding)
        setup_static_resources.add_catch(fail_onboarding)

        setup_prerequisites_resources.next(
            setup_dynamic_resources.next(setup_static_resources.next(complete_onboarding.next(success)))
        )

        # Log Group
        log_group = aws_logs.LogGroup(
            self,
            "AccountOnboardingLogGroup",
            log_group_name=app_config.format_resource_name("account-onboarding-state-machine-log-group"),
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
            retention=aws_logs.RetentionDays.TWO_MONTHS,
        )

        # State Machine
        claim_or_legacy_event = sfn.Choice(self, "HasOnboardingOperationId")
        claim_or_legacy_event.when(
            sfn.Condition.is_present("$.onboardingOperationId"),
            claim_operation.next(setup_prerequisites_resources),
        ).otherwise(setup_prerequisites_resources)

        self._state_machine: sfn.StateMachine = sfn.StateMachine(
            self,
            "AccountOnboardingStateMachine",
            state_machine_name=app_config.format_resource_name("account-onboarding-state-machine"),
            logs=sfn.LogOptions(destination=log_group, level=sfn.LogLevel.ALL),
            tracing_enabled=True,
            definition_body=sfn.DefinitionBody.from_chainable(
                start.next(claim_or_legacy_event),
            ),
        )

        # cdk-nag Suppressions
        cdk_nag.NagSuppressions.add_resource_suppressions(
            construct=log_group,
            suppressions=[
                cdk_nag.NagPackSuppression(
                    id="NIST.800.53.R4-CloudWatchLogGroupEncrypted",
                    reason="Log group is encrypted with default master key.",
                ),
                cdk_nag.NagPackSuppression(
                    id="NIST.800.53.R5-CloudWatchLogGroupEncrypted",
                    reason="Log group is encrypted with default master key.",
                ),
                cdk_nag.NagPackSuppression(
                    id="PCI.DSS.321-CloudWatchLogGroupEncrypted",
                    reason="Log group is encrypted with default master key.",
                ),
            ],
        )

        state_machine_role_policy = [
            p for p in self._state_machine.role.node.children if isinstance(p, aws_iam.Policy)
        ][0]

        cdk_nag.NagSuppressions.add_resource_suppressions(
            construct=state_machine_role_policy,
            suppressions=[
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-IAM5",
                    reason="This policy is autogenerated by CDK with minimal permissions.",
                ),
                cdk_nag.NagPackSuppression(
                    id="NIST.800.53.R4-IAMNoInlinePolicy",
                    reason="This is an inline policy auto-generated by CDK.",
                ),
                cdk_nag.NagPackSuppression(
                    id="NIST.800.53.R5-IAMNoInlinePolicy",
                    reason="This is an inline policy auto-generated by CDK.",
                ),
                cdk_nag.NagPackSuppression(
                    id="PCI.DSS.321-IAMNoInlinePolicy",
                    reason="This is an inline policy auto-generated by CDK.",
                ),
            ],
        )

    @property
    def state_machine(self):
        return self._state_machine
