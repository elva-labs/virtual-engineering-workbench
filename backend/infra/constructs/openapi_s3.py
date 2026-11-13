"""A REST API's OpenAPI definition deployed from S3 (`BodyS3Location`), not inline in the template.

CloudFormation refuses a template over 1,000,000 bytes. An inline `Body` puts the whole rendered OpenAPI document
into the stack's template (twice for the portal API, which also serves its own `openapi.json`), and the largest
bounded contexts' definitions are several hundred KB each: the provisioning stack reaches the limit with a few more
routes. The definition becomes a CDK file asset instead. API Gateway imports that file as it is: CloudFormation
doesn't resolve intrinsic functions or `${AWS::Region}` in it (it did in an inline `Body`), so everything in the file
is a literal string at synth time:
- the Lambda ARNs and the API Gateway role ARN are built from their explicit names (the functions, aliases and the
  role are named in the constructs);
- the pseudo parameters (`${AWS::Region}`, ...) are replaced with the stack's values;
- anything still unresolved (a CDK token, a deploy-time SSM value) fails the synth instead of reaching API Gateway.

A changed definition is a new asset (its key is the content hash), so the stack's `BodyS3Location` changes and CDK's
latest deployment gets a new logical id: a definition change still deploys the stage, as it did with `Body`.
"""

import json
import os
import re
import tempfile
import typing

import aws_cdk
import constructs
from aws_cdk import aws_apigateway, aws_iam, aws_lambda, region_info

from infra import config, constants

_PSEUDO_PARAMETER = re.compile(r"\$\{AWS::[A-Za-z]+\}")


def partition(region: str) -> str:
    return region_info.RegionInfo.get(region).partition or "aws"


def _literal(value: typing.Any, what: str) -> str:
    if not isinstance(value, str) or aws_cdk.Token.is_unresolved(value):
        raise ValueError(f"{what} must be a name known at synth time for an API definition in S3, got {value!r}")
    return value


def function_arn(handler: typing.Union[aws_lambda.Function, aws_lambda.Alias], account: str, region: str) -> str:
    """The (qualified) ARN of a function or alias whose name is set explicitly, as a literal string."""
    if isinstance(handler, aws_lambda.Alias):
        name = _literal(handler.version.lambda_.node.default_child.function_name, "function name")
        qualifier = ":" + _literal(handler.node.default_child.name, "alias name")
    else:
        name, qualifier = _literal(handler.node.default_child.function_name, "function name"), ""
    return f"arn:{partition(region)}:lambda:{region}:{account}:function:{name}{qualifier}"


def role_arn(role: aws_iam.Role, account: str, region: str) -> str:
    cfn_role: aws_iam.CfnRole = role.node.default_child
    name = _literal(cfn_role.role_name, "role name")
    return f"arn:{partition(region)}:iam::{account}:role{cfn_role.path or '/'}{name}"


def authorizer_arn(app_config: config.AppConfig) -> str:
    """The authorization context's API authorizer (its `live` alias), which AuthorizationAppStack names the same way
    and publishes in SSM (`authorizer-arn`) for consumers that resolve it at deployment time."""
    name = app_config.format_resource_name_with_component(
        constants.AUTH_BC_NAME, constants.AUTH_BC_AUTHORIZER_ENTRYPOINT
    )
    return (
        f"arn:{partition(app_config.region)}:lambda:{app_config.region}:{app_config.account}"
        f":function:{name}:{constants.LAMBDA_ALIAS_NAME}"
    )


def literal_document(document: typing.Any, account: str, region: str) -> str:
    """The definition as JSON, with the pseudo parameters replaced and nothing left to resolve at deployment."""
    text = json.dumps(document, sort_keys=False, default=str)
    if aws_cdk.Token.is_unresolved(text):
        raise ValueError("the API definition holds a CDK token; an API definition in S3 is not resolved at deployment")
    values = {
        "${AWS::Region}": region,
        "${AWS::AccountId}": account,
        "${AWS::Partition}": partition(region),
        "${AWS::URLSuffix}": region_info.RegionInfo.get(region).domain_suffix or "amazonaws.com",
    }
    text = _PSEUDO_PARAMETER.sub(lambda match: values.get(match.group(0), match.group(0)), text)
    if left := sorted(set(_PSEUDO_PARAMETER.findall(text))):
        raise ValueError(f"the API definition uses pseudo parameters with no synth-time value: {', '.join(left)}")
    return text


def asset_api_definition(scope: constructs.Construct, name: str, document: typing.Any) -> aws_apigateway.ApiDefinition:
    """An `ApiDefinition` from a file asset holding `document` (see the module docstring)."""
    stack = aws_cdk.Stack.of(scope)
    account = _literal(stack.account, "stack account")
    region = _literal(stack.region, "stack region")
    directory = tempfile.mkdtemp(prefix="vew-openapi-")
    path = os.path.join(directory, f"{name}.json")
    with open(path, "w", encoding="utf-8") as file:
        file.write(literal_document(document, account, region))
    return aws_apigateway.ApiDefinition.from_asset(path)
