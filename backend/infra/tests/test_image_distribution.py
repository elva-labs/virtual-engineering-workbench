"""Image distribution modes (env config "image-distribution"): "share" by default, "store-restore" opt-in."""

import copy
import json
import os

import assertpy
import aws_cdk
import pytest
from aws_cdk import assertions, aws_lambda

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from infra import config, constants  # noqa: E402
from infra.constructs.ami_sharing import ami_sharing_state_machine  # noqa: E402
from infra.helpers.image_distribution import image_distribution  # noqa: E402
from infra.usecase import product_publishing_enablement_app_stack  # noqa: E402

ACCOUNT = "111111111111"
STORE_STATES = {"StoreAmiLambda", "VerifyStoreLambda", "RestoreAmiLambda", "VerifyRestoreLambda"}


def _app_config(component: str, mode: str | None, store_with_function_role: bool = False) -> config.AppConfig:
    env = copy.deepcopy(config.env_config["dev"])
    if mode is None:
        env.pop("image-distribution", None)
    else:
        env["image-distribution"] = {"mode": mode, "storeWithFunctionRole": store_with_function_role}
    return config.AppConfig(
        account=ACCOUNT,
        region="us-east-1",
        environment="dev",
        web_app_account=ACCOUNT,
        component_name=component,
        environment_config=env,
        component_specific={},
    )


def _state_names(mode: str | None) -> set[str]:
    app = aws_cdk.App()
    stack = aws_cdk.Stack(app, "Test", env=aws_cdk.Environment(account=ACCOUNT, region="us-east-1"))
    fn = aws_lambda.Function(
        stack,
        "Fn",
        runtime=aws_lambda.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=aws_lambda.Code.from_inline("def handler(e, c): pass"),
    )
    ami_sharing_state_machine.AmiSharingStateMachine(
        stack, "AmiSharing", app_config=_app_config("publishing", mode), ami_sharing_lambda=fn
    )
    machine = next(
        iter(assertions.Template.from_stack(stack).find_resources("AWS::StepFunctions::StateMachine").values())
    )
    definition = machine["Properties"]["DefinitionString"]
    text = json.dumps(definition)
    return {name for name in STORE_STATES | {"ShareAmiLambda", "PassDistributed"} if f'\\"{name}\\"' in text}


def test_share_is_the_default():
    assertpy.assert_that(image_distribution(_app_config("publishing", None))["mode"]).is_equal_to("share")
    names = _state_names(None)
    assertpy.assert_that(names).contains("ShareAmiLambda")
    assertpy.assert_that(names & STORE_STATES).is_empty()


def test_store_restore_moves_the_image_before_recording_it():
    names = _state_names("store-restore")
    assertpy.assert_that(names).contains("ShareAmiLambda", "PassDistributed", *STORE_STATES)


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        image_distribution(_app_config("publishing", "copy-everywhere"))


@pytest.mark.parametrize("mode,buckets", [("share", 0), ("store-restore", 1)])
def test_target_accounts_get_the_import_bucket_only_in_store_restore(mode, buckets):
    app = aws_cdk.App()
    stack = product_publishing_enablement_app_stack.ProductPublishingEnablementAppStack(
        app,
        "Enablement",
        app_config=_app_config("product-publishing-enablement", mode),
        image_service_account_id="222222222222",
        catalog_service_account_id="333333333333",
        web_application_account=ACCOUNT,
        env=aws_cdk.Environment(account="444444444444", region="us-east-1"),
    )
    template = assertions.Template.from_stack(stack)
    template.resource_count_is("AWS::S3::Bucket", buckets)
    roles = [
        r["Properties"].get("RoleName")
        for r in template.find_resources("AWS::IAM::Role").values()
        if r["Properties"].get("RoleName") == constants.PRODUCT_PUBLISHING_IMAGE_IMPORT_ROLE
    ]
    assertpy.assert_that(roles).is_length(buckets)
    if buckets:
        policy = next(iter(template.find_resources("AWS::S3::BucketPolicy").values()))
        writers = json.dumps(policy["Properties"]["PolicyDocument"])
        # Without storeWithFunctionRole the image service account's role writes the stored image.
        assertpy.assert_that(writers).contains(f"222222222222:role/{constants.PRODUCT_PUBLISHING_IMAGE_SERVICE_ROLE}")
