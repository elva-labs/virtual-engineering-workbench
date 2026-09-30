import pathlib

import cdk_nag
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_ec2,
    aws_events,
    aws_events_targets,
    aws_iam,
    aws_lambda,
    aws_logs,
    aws_ssm,
)
from constructs import Construct

from infra import config, constants


class ProvisioningEnablementStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        app_config: config.AppConfig,
        web_application_account: str,
        web_application_region: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # EC2 events forwarder rule
        aws_events.Rule(
            self,
            "ec2-events-forwarder-rule",
            rule_name=app_config.format_resource_name("ec2-evt-fw-rule"),
            event_pattern=aws_events.EventPattern(
                source=aws_events.Match.exact_string("aws.ec2"),
                detail_type=aws_events.Match.exact_string("EC2 Instance State-change Notification"),
                account=aws_events.Match.exact_string(self.account),
            ),
            targets=[
                aws_events_targets.EventBus(
                    aws_events.EventBus.from_event_bus_arn(
                        self,
                        "provisioning-ec2-event-bus",
                        self.format_arn(
                            resource="event-bus",
                            service="events",
                            account=web_application_account,
                            partition=self.partition,
                            region=web_application_region,
                            resource_name=app_config.format_resource_name_with_component(
                                "provisioning", "ec2-event-bus"
                            ),
                        ),
                    )
                )
            ],
        )

        # ECS events forwarder rule
        aws_events.Rule(
            self,
            "ecs-events-forwarder-rule",
            rule_name=app_config.format_resource_name("ecs-evt-fw-rule"),
            event_pattern=aws_events.EventPattern(
                source=aws_events.Match.exact_string("aws.ecs"),
                detail_type=aws_events.Match.exact_string("ECS Task State Change"),
                account=aws_events.Match.exact_string(self.account),
            ),
            targets=[
                aws_events_targets.EventBus(
                    aws_events.EventBus.from_event_bus_arn(
                        self,
                        "provisioning-ecs-event-bus",
                        self.format_arn(
                            resource="event-bus",
                            service="events",
                            account=web_application_account,
                            partition=self.partition,
                            region=web_application_region,
                            resource_name=app_config.format_resource_name_with_component(
                                "provisioning", "ecs-event-bus"
                            ),
                        ),
                    )
                )
            ],
        )

        # Provisioned product task role managed policy
        aws_iam.ManagedPolicy(
            self,
            "ProvisionedProductTaskRolePermissionsPolicy",
            managed_policy_name=constants.PROVISIONED_PRODUCT_TASK_ROLE_POLICY,
            description="This managed policy contains centrally managed IAM permissions for all VEW Containers.",
            statements=[
                aws_iam.PolicyStatement(
                    effect=aws_iam.Effect.ALLOW,
                    actions=["s3:Get*", "s3:List*"],
                    resources=[
                        "arn:aws:s3:::*-repository.vew",
                        "arn:aws:s3:::*-repository.vew/*",
                    ],
                )
            ],
        )

        # Provisioned product instance profile managed policy
        instance_profile_statements = [
            aws_iam.PolicyStatement(
                effect=aws_iam.Effect.ALLOW,
                actions=[
                    "ec2:ModifyInstanceMetadataOptions",
                    "ec2:DescribeInstances",
                ],
                resources=["*"],
            ),
        ]

        aws_iam.ManagedPolicy(
            self,
            "ProvisionedProductInstanceProfilePermissionsPolicy",
            managed_policy_name=constants.PROVISIONED_PRODUCT_INSTANCE_PROFILE_POLICY,
            description="This managed policy contains centrally managed IAM permissions for all VEW Workbench and Virtual Target instances.",
            statements=instance_profile_statements,
        )

        # Provisioned product security group
        vpc_id = aws_ssm.StringParameter.value_from_lookup(
            self, app_config.environment_config["spoke-account-vpc-id-param-name"]
        )
        vpc = aws_ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)

        provisioned_product_sg = aws_ec2.SecurityGroup(
            self,
            "ProvisionedProductSecutiryGroup",
            vpc=vpc,
            allow_all_outbound=False,
            security_group_name=app_config.format_resource_name("pp-sg"),
            description="Contains centrally managed security group rules for all workbenches and virtual targets",
        )

        aws_ssm.StringParameter(
            self,
            "ProvisionedProductSG",
            description="Security Group ID for all provisioned products",
            parameter_name=app_config.format_ssm_parameter_name(name="pp-sg", include_environment=False),
            string_value=provisioned_product_sg.security_group_id,
        )

        # Workbench instance role cleanup: an organization's SSM Quick Setup patch policy, for example,
        # attaches AWSQuickSetupPatchPolicyBaselineAccess to every instance role, and CloudFormation cannot
        # delete a role with policies it did not attach.
        # The product templates call this function from an InstanceRoleCleanup custom resource that is
        # deleted after the instance and before the role; it removes only what the template does not
        # declare.
        cleanup_role = aws_iam.Role(
            self,
            "WorkbenchRoleCleanupRole",
            role_name=constants.WORKBENCH_ROLE_CLEANUP_FUNCTION,
            assumed_by=aws_iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "StripForeignPolicies": aws_iam.PolicyDocument(
                    statements=[
                        # Only the roles of Service Catalog product stacks (SC-<account>-pp-*).
                        aws_iam.PolicyStatement(
                            actions=[
                                "iam:ListAttachedRolePolicies",
                                "iam:DetachRolePolicy",
                                "iam:ListRolePolicies",
                                "iam:DeleteRolePolicy",
                            ],
                            resources=[f"arn:{self.partition}:iam::{self.account}:role/SC-{self.account}-pp-*"],
                        ),
                    ]
                ),
            },
        )
        cleanup_logs = aws_logs.LogGroup(
            self,
            "WorkbenchRoleCleanupLogs",
            log_group_name=f"/aws/lambda/{constants.WORKBENCH_ROLE_CLEANUP_FUNCTION}",
            retention=aws_logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY,
        )
        cleanup_logs.grant_write(cleanup_role)
        cleanup_function = aws_lambda.Function(
            self,
            "WorkbenchRoleCleanup",
            function_name=constants.WORKBENCH_ROLE_CLEANUP_FUNCTION,
            description="Removes policies attached from outside a workbench template before its role is deleted",
            runtime=aws_lambda.Runtime.PYTHON_3_14,
            architecture=constants.LAMBDA_ARCHITECTURE,
            handler="index.handler",
            # Inline (ZipFile, standard library + boto3 only): no asset upload into the spoke.
            code=aws_lambda.Code.from_inline(
                (pathlib.Path(__file__).parent / "resources" / "workbench_role_cleanup.py").read_text()
            ),
            timeout=Duration.seconds(60),
            role=cleanup_role,
            log_group=cleanup_logs,
        )
        aws_ssm.StringParameter(
            self,
            "WorkbenchRoleCleanupFunctionArn",
            description="Custom resource function the workbench templates use before deleting their instance role",
            parameter_name=constants.WORKBENCH_ROLE_CLEANUP_FUNCTION_ARN_PARAMETER,
            string_value=cleanup_function.function_arn,
        )
        cdk_nag.NagSuppressions.add_resource_suppressions(
            cleanup_function,
            [
                cdk_nag.NagPackSuppression(id=rule, reason=reason)
                for rule, reason in {
                    "NIST.800.53.R4-LambdaInsideVPC": "Calls only IAM and the CloudFormation response URL; no VPC resources.",
                    "NIST.800.53.R5-LambdaInsideVPC": "Calls only IAM and the CloudFormation response URL; no VPC resources.",
                    "PCI.DSS.321-LambdaInsideVPC": "Calls only IAM and the CloudFormation response URL; no VPC resources.",
                    "NIST.800.53.R5-LambdaConcurrency": "Invoked once per workbench stack delete; no concurrency limit needed.",
                    "NIST.800.53.R5-LambdaDLQ": "Synchronous CloudFormation custom resource; failures surface on the stack.",
                }.items()
            ],
        )
        cdk_nag.NagSuppressions.add_resource_suppressions(
            cleanup_logs,
            [
                cdk_nag.NagPackSuppression(id=rule, reason="Holds only role and policy names; default encryption.")
                for rule in (
                    "NIST.800.53.R4-CloudWatchLogGroupEncrypted",
                    "NIST.800.53.R5-CloudWatchLogGroupEncrypted",
                    "PCI.DSS.321-CloudWatchLogGroupEncrypted",
                )
            ],
        )

        # Stack based suppressions
        cdk_nag.NagSuppressions.add_stack_suppressions(
            stack=Stack.of(self),
            suppressions=[
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-IAM5",
                    reason="This is an inline policy auto-generated by CDK.",
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
