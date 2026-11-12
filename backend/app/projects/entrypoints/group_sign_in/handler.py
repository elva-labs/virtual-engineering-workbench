import os

import boto3
from aws_lambda_powertools import Logger

from app.projects.entrypoints.group_sign_in import bootstrapper
from app.shared.identity import oidc_groups

logger = Logger()
dependencies = None


def handler(event, context):
    if not oidc_groups.provider_name():
        return
    if event.get("userPoolId") != os.environ["COGNITO_USER_POOL_ID"]:
        raise ValueError("Unexpected user pool")
    if event.get("triggerSource") not in {"TokenGeneration_HostedAuth", "TokenGeneration_Authentication"}:
        return
    # Read the latest profile rather than a queued snapshot. Retries and delayed
    # sign-in events must not restore groups removed at a newer sign-in.
    profile = boto3.client("cognito-idp").admin_get_user(UserPoolId=event["userPoolId"], Username=event["userName"])
    if not profile.get("Enabled", False):
        return
    attributes = {attribute["Name"]: attribute["Value"] for attribute in profile["UserAttributes"]}
    user_id = attributes.get("custom:user_tid")
    if not user_id:
        return
    global dependencies
    if dependencies is None:
        dependencies = bootstrapper.bootstrap(logger)
    dependencies.group_membership_service.sign_in(user_id, attributes)
