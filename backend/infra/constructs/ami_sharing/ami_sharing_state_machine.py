import aws_cdk
import cdk_nag
import constructs
from aws_cdk import aws_iam, aws_lambda, aws_logs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as sfn_tasks

from infra import config
from infra.helpers.image_distribution import image_distribution


class AmiSharingStateMachine(constructs.Construct):
    def __init__(
        self,
        scope: constructs.Construct,
        id: str,
        app_config: config.AppConfig,
        ami_sharing_lambda: aws_lambda.Function,
    ) -> None:
        """
        Sample expected input for the state machine:
        {
            "version": "0",
            "id": "a0fd2d09-02c3-1696-1cee-406e4cc14c86",
            "detail-type": "ProductVersionCreationStarted",
            "source": "proserve.workbench.publishing.dev",
            "account": "201223934255",
            "time": "2023-08-01T14:18:48Z",
            "region": "us-east-1",
            "resources": [],
            "detail": {
                "eventName": "ProductVersionCreationStarted",
                "productId": "prod-dt3ycosm",
                "versionId": "vers-1vbc9box",
                "awsAccountId": "105249321508",
                "stage": "DEV",  # ADR 0013: a distribution is per account and stage
                "oldVersionId": "vers-12345abc"  # Only in ProductVersionRestorationStarted
                "productType": "VIRTUAL_TARGET"
            }
        }
        """
        super().__init__(scope, id)
        image_distribution_mode = image_distribution(app_config)["mode"]

        # Starting state
        start_state: sfn.Pass = sfn.Pass(self, "StartState", output_path="$.detail")

        # Success State
        success_state: sfn.Succeed = sfn.Succeed(self, "SuccessState")

        # Fail State
        fail_state: sfn.Fail = sfn.Fail(self, "FailState")

        # Pass states for storing copied ami id
        pass_copy: sfn.Pass = sfn.Pass(
            self, "PassCopy", parameters={"copiedAmiId.$": "$.copyAmiResponse.copiedAmiId"}, result_path="$.copiedAmi"
        )
        pass_share: sfn.Pass = sfn.Pass(
            self,
            "PassShare",
            parameters={"copiedAmiId.$": "$.decideActionResponse.originalAmiId"},
            result_path="$.copiedAmi",
        )
        pass_done: sfn.Pass = sfn.Pass(
            self,
            "PassDone",
            parameters={"copiedAmiId.$": "$.decideActionResponse.copiedAmiId"},
            result_path="$.copiedAmi",
        )

        # Pass state for assigning old version id
        pass_assign_old_version_id: sfn.Pass = sfn.Pass(
            self,
            "PassAssignOldVersionId",
            parameters={
                "eventName.$": "$.eventName",
                "productId.$": "$.productId",
                "versionId.$": "$.versionId",
                "awsAccountId.$": "$.awsAccountId",
                "oldVersionId": "",
            },
            result_path="$.PassOldVersionIdResponse",
        )

        # Pass state for doing nothing in case oldVersionId is already assigned
        # Special thanks to how state machines work
        pass_skip_old_version_id: sfn.Pass = sfn.Pass(
            self,
            "PassSkipOldVersionId",
            parameters={
                "eventName.$": "$.eventName",
                "productId.$": "$.productId",
                "versionId.$": "$.versionId",
                "awsAccountId.$": "$.awsAccountId",
                "oldVersionId": "$.oldVersionId",
            },
            result_path="$.PassOldVersionIdResponse",
        )

        # Fail ami sharing lambda
        fail_ami_sharing_lambda: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "FailAmiSharingLambda",
            lambda_function=ami_sharing_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "FailAmiSharingRequest",
                    "productId.$": "$.productId",
                    "versionId.$": "$.versionId",
                    "awsAccountId.$": "$.awsAccountId",
                    "stage.$": "$.stage",
                }
            ),
            result_selector={"eventType.$": "$.Payload.eventType"},
            result_path="$.failAmiSharingResponse",
        )

        # Decide action lambda
        decide_action_lambda: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "DecideActionLambda",
            lambda_function=ami_sharing_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "DecideActionRequest",
                    "productId.$": "$.productId",
                    "versionId.$": "$.versionId",
                    "awsAccountId.$": "$.awsAccountId",
                    "stage.$": "$.stage",
                    "productType.$": "$.productType",
                }
            ),
            result_selector={
                "eventType.$": "$.Payload.eventType",
                "decision.$": "$.Payload.decision",
                "originalAmiId.$": "$.Payload.originalAmiId",
                "copiedAmiId.$": "$.Payload.copiedAmiId",
                "region.$": "$.Payload.region",
            },
            result_path="$.decideActionResponse",
        )
        decide_action_lambda.add_catch(fail_ami_sharing_lambda, result_path="$.error")

        # Copy ami lambda
        copy_ami_lambda: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "CopyAmiLambda",
            lambda_function=ami_sharing_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "CopyAmiRequest",
                    "originalAmiId.$": "$.decideActionResponse.originalAmiId",
                    "region.$": "$.decideActionResponse.region",
                }
            ),
            result_selector={
                "eventType.$": "$.Payload.eventType",
                "copiedAmiId.$": "$.Payload.copiedAmiId",
            },
            result_path="$.copyAmiResponse",
        )
        copy_ami_lambda.add_catch(fail_ami_sharing_lambda, result_path="$.error")

        # Verify copy lambda
        verify_copy_lambda: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "VerifyCopyLambda",
            lambda_function=ami_sharing_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "VerifyCopyRequest",
                    "region.$": "$.decideActionResponse.region",
                    "copiedAmiId.$": "$.copiedAmi.copiedAmiId",
                }
            ),
            result_selector={
                "eventType.$": "$.Payload.eventType",
                "isCopyVerified.$": "$.Payload.isCopyVerified",
            },
            result_path="$.verifyCopyResponse",
        )
        verify_copy_lambda.add_catch(fail_ami_sharing_lambda, result_path="$.error")

        # Share ami lambda
        share_ami_lambda: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "ShareAmiLambda",
            lambda_function=ami_sharing_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "ShareAmiRequest",
                    "originalAmiId.$": "$.decideActionResponse.originalAmiId",
                    "copiedAmiId.$": "$.copiedAmi.copiedAmiId",
                    "region.$": "$.decideActionResponse.region",
                    "awsAccountId.$": "$.awsAccountId",
                }
            ),
            result_selector={"eventType.$": "$.Payload.eventType"},
            result_path="$.shareAmiResponse",
        )
        share_ami_lambda.add_catch(fail_ami_sharing_lambda, result_path="$.error")

        # "share" distribution shares the copy with the account; "store-restore" moves the image into
        # the account first and then only records the account's own copy (env config
        # "image-distribution").
        if image_distribution_mode == "store-restore":
            distribute = self._store_restore_chain(ami_sharing_lambda, fail_ami_sharing_lambda, share_ami_lambda)
        else:
            distribute = share_ami_lambda

        # Succeed ami sharing lambda
        succeed_ami_sharing_lambda: sfn_tasks.LambdaInvoke = sfn_tasks.LambdaInvoke(
            self,
            "SucceedAmiSharingLambda",
            lambda_function=ami_sharing_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "eventType": "SucceedAmiSharingRequest",
                    "productId.$": "$.productId",
                    "versionId.$": "$.versionId",
                    "awsAccountId.$": "$.awsAccountId",
                    "stage.$": "$.stage",
                    "copiedAmiId.$": "$.copiedAmi.copiedAmiId",
                    "previousEventName.$": "$.eventName",
                    "oldVersionId.$": "$.PassOldVersionIdResponse.oldVersionId",
                    "productType.$": "$.productType",
                }
            ),
            result_selector={"eventType.$": "$.Payload.eventType"},
            result_path="$.succeedAmiSharingResponse",
        )
        succeed_ami_sharing_lambda.add_catch(fail_ami_sharing_lambda, result_path="$.error")

        # Wait for verifying copy
        verify_copy_wait: sfn.Wait = sfn.Wait(
            self, "VerifyCopyWait", time=sfn.WaitTime.duration(aws_cdk.Duration.seconds(30))
        )

        # Check old version id choice
        check_old_version_id_choice: sfn.Choice = sfn.Choice(self, "CheckOldVersionIdChoice")
        check_old_version_id_choice.when(sfn.Condition.is_not_present("$.oldVersionId"), pass_assign_old_version_id)
        check_old_version_id_choice.otherwise(pass_skip_old_version_id)

        # Verify copy choice
        verify_copy_choice: sfn.Choice = sfn.Choice(self, "VerifyCopyChoice")
        verify_copy_choice.when(sfn.Condition.boolean_equals("$.verifyCopyResponse.isCopyVerified", True), distribute)
        verify_copy_choice.otherwise(verify_copy_wait)
        verify_copy_wait.next(verify_copy_lambda)

        # Define the 3 chains: COPY/SHARE/DONE
        chain_copy = (
            sfn.Chain.start(copy_ami_lambda)
            .next(pass_copy)
            .next(verify_copy_lambda)
            .next(verify_copy_choice.afterwards())
        )
        chain_share = sfn.Chain.start(pass_share).next(distribute)
        chain_done = sfn.Chain.start(pass_done)
        fail_ami_sharing_lambda.next(fail_state)

        # Decide action choice
        decide_action_choice: sfn.Choice = sfn.Choice(self, "DecideActionChoice")
        decide_action_choice.when(sfn.Condition.string_equals("$.decideActionResponse.decision", "COPY"), chain_copy)
        decide_action_choice.when(sfn.Condition.string_equals("$.decideActionResponse.decision", "SHARE"), chain_share)
        decide_action_choice.when(sfn.Condition.string_equals("$.decideActionResponse.decision", "DONE"), chain_done)
        decide_action_choice.when(
            sfn.Condition.string_equals("$.decideActionResponse.decision", "NOT_REQUIRED"), chain_done
        )
        decide_action_choice.otherwise(fail_ami_sharing_lambda)

        # Define log group
        log_group = aws_logs.LogGroup(
            self,
            "AmiSharingStateMachineLogGroup",
            log_group_name=app_config.format_resource_name("ami-sharing-state-machine-log-group"),
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
            retention=aws_logs.RetentionDays.TWO_MONTHS,
        )

        # Define the state machine
        self._state_machine: sfn.StateMachine = sfn.StateMachine(
            self,
            "AmiSharingStateMachine",
            state_machine_name=app_config.format_resource_name("ami-sharing-state-machine"),
            logs=sfn.LogOptions(destination=log_group, level=sfn.LogLevel.ALL),
            tracing_enabled=True,
            definition_body=sfn.DefinitionBody.from_chainable(
                sfn.Chain.start(start_state)
                .next(check_old_version_id_choice.afterwards())
                .next(decide_action_lambda)
                .next(decide_action_choice.afterwards())
                .next(succeed_ami_sharing_lambda)
                .next(success_state)
            ),
        )

        # cdk-nag suppressions
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
                    id="AwsSolutions-IAM5", reason="This policy is autogenerated by CDK with minimal permissions."
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

    def _store_restore_chain(
        self,
        ami_sharing_lambda: aws_lambda.IFunction,
        fail_ami_sharing_lambda: sfn_tasks.LambdaInvoke,
        record_ami_lambda: sfn_tasks.LambdaInvoke,
    ) -> sfn.IChainable:
        """store -> verify store -> restore -> verify restore -> record; the account's own image then
        replaces $.copiedAmi for the rest of the flow."""

        def invoke(name: str, payload: dict, result: dict, result_path: str) -> sfn_tasks.LambdaInvoke:
            task = sfn_tasks.LambdaInvoke(
                self,
                name,
                lambda_function=ami_sharing_lambda,
                payload=sfn.TaskInput.from_object(payload),
                result_selector={"eventType.$": "$.Payload.eventType", **result},
                result_path=result_path,
            )
            task.add_catch(fail_ami_sharing_lambda, result_path="$.error")
            return task

        store = invoke(
            "StoreAmiLambda",
            {
                "eventType": "StoreAmiRequest",
                "sourceAmiId.$": "$.copiedAmi.copiedAmiId",
                "region.$": "$.decideActionResponse.region",
                "awsAccountId.$": "$.awsAccountId",
            },
            {"objectKey.$": "$.Payload.objectKey"},
            "$.storeAmiResponse",
        )
        verify_store = invoke(
            "VerifyStoreLambda",
            {
                "eventType": "VerifyStoreRequest",
                "sourceAmiId.$": "$.copiedAmi.copiedAmiId",
                "region.$": "$.decideActionResponse.region",
            },
            {"isStoreVerified.$": "$.Payload.isStoreVerified"},
            "$.verifyStoreResponse",
        )
        restore = invoke(
            "RestoreAmiLambda",
            {
                "eventType": "RestoreAmiRequest",
                "originalAmiId.$": "$.decideActionResponse.originalAmiId",
                "objectKey.$": "$.storeAmiResponse.objectKey",
                "region.$": "$.decideActionResponse.region",
                "awsAccountId.$": "$.awsAccountId",
            },
            {"distributedAmiId.$": "$.Payload.distributedAmiId"},
            "$.restoreAmiResponse",
        )
        verify_restore = invoke(
            "VerifyRestoreLambda",
            {
                "eventType": "VerifyRestoreRequest",
                "distributedAmiId.$": "$.restoreAmiResponse.distributedAmiId",
                "region.$": "$.decideActionResponse.region",
                "awsAccountId.$": "$.awsAccountId",
            },
            {"isRestoreVerified.$": "$.Payload.isRestoreVerified"},
            "$.verifyRestoreResponse",
        )
        # From here on the account's own image is the one recorded and launched.
        pass_distributed = sfn.Pass(
            self,
            "PassDistributed",
            parameters={"copiedAmiId.$": "$.restoreAmiResponse.distributedAmiId"},
            result_path="$.copiedAmi",
        )

        store_wait = sfn.Wait(self, "VerifyStoreWait", time=sfn.WaitTime.duration(aws_cdk.Duration.seconds(60)))
        restore_wait = sfn.Wait(self, "VerifyRestoreWait", time=sfn.WaitTime.duration(aws_cdk.Duration.seconds(60)))

        verify_restore_choice = sfn.Choice(self, "VerifyRestoreChoice")
        verify_restore_choice.when(
            sfn.Condition.boolean_equals("$.verifyRestoreResponse.isRestoreVerified", True),
            sfn.Chain.start(pass_distributed).next(record_ami_lambda),
        )
        verify_restore_choice.otherwise(restore_wait)
        restore_wait.next(verify_restore)

        verify_store_choice = sfn.Choice(self, "VerifyStoreChoice")
        verify_store_choice.when(
            sfn.Condition.boolean_equals("$.verifyStoreResponse.isStoreVerified", True),
            sfn.Chain.start(restore).next(verify_restore).next(verify_restore_choice),
        )
        verify_store_choice.otherwise(store_wait)
        store_wait.next(verify_store)

        return sfn.Chain.start(store).next(verify_store).next(verify_store_choice)
