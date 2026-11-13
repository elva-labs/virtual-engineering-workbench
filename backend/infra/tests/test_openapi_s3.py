"""REST API definitions deploy from S3 (BodyS3Location), with literal values only, and a changed definition still
deploys the stage."""

import json
import pathlib

import aws_cdk
import pytest
from aws_cdk import aws_apigateway, aws_iam, aws_lambda, aws_ssm

from infra import config, constants
from infra.backend import authorization_app_stack
from infra.constructs import openapi_s3

ACCOUNT, REGION = "111122223333", "eu-north-1"


def _document(handler_arn, role_arn, summary="Get a thing"):
    return {
        "openapi": "3.0.1",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/things": {
                "get": {
                    "summary": summary,
                    "responses": {"200": {"description": "ok"}},
                    "x-amazon-apigateway-integration": {
                        "type": "aws_proxy",
                        "httpMethod": "POST",
                        "uri": f"arn:aws:apigateway:${{AWS::Region}}:lambda:path/2015-03-31/functions/{handler_arn}/invocations",
                        "credentials": role_arn,
                    },
                }
            }
        },
    }


def _synth(summary="Get a thing"):
    app = aws_cdk.App(outdir=None)
    stack = aws_cdk.Stack(app, "Api", env=aws_cdk.Environment(account=ACCOUNT, region=REGION))
    function = aws_lambda.Function(
        stack,
        "Handler",
        function_name="proserve-wb-test-api-dev",
        runtime=aws_lambda.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=aws_lambda.Code.from_inline("def handler(event, context): pass"),
    )
    alias = function.add_alias(constants.LAMBDA_ALIAS_NAME)
    role = aws_iam.Role(
        stack,
        "Role",
        role_name="proserve-wb-test-apigw-role-dev",
        assumed_by=aws_iam.ServicePrincipal("apigateway.amazonaws.com"),
    )
    handler_arn = openapi_s3.function_arn(alias, ACCOUNT, REGION)
    role_arn = openapi_s3.role_arn(role, ACCOUNT, REGION)
    api = aws_apigateway.SpecRestApi(
        stack,
        "Api",
        api_definition=openapi_s3.asset_api_definition(stack, "api", _document(handler_arn, role_arn, summary)),
        endpoint_types=[aws_apigateway.EndpointType.PRIVATE],
    )
    assembly = app.synth()
    template = assembly.get_stack_by_name("Api").template
    return handler_arn, role_arn, api, template, pathlib.Path(assembly.directory)


def _resources(template, kind):
    return {name: r for name, r in template["Resources"].items() if r["Type"] == kind}


def test_the_definition_is_an_s3_asset_with_literal_values():
    handler_arn, role_arn, _, template, directory = _synth()

    assert (
        handler_arn
        == f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:proserve-wb-test-api-dev:{constants.LAMBDA_ALIAS_NAME}"
    )
    assert role_arn == f"arn:aws:iam::{ACCOUNT}:role/proserve-wb-test-apigw-role-dev"
    (api,) = _resources(template, "AWS::ApiGateway::RestApi").values()
    assert "Body" not in api["Properties"]
    location = api["Properties"]["BodyS3Location"]
    definition = json.loads((directory / f"asset.{location['Key']}").read_text())
    integration = definition["paths"]["/things"]["get"]["x-amazon-apigateway-integration"]
    # CloudFormation resolved ${AWS::Region} in an inline Body; from S3 nothing is resolved, so it's done at synth.
    assert (
        integration["uri"] == f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{handler_arn}/invocations"
    )
    assert integration["credentials"] == role_arn


def test_a_changed_definition_deploys_the_stage_again():
    _, _, _, before, _ = _synth()
    _, _, _, same, _ = _synth()
    _, _, _, after, _ = _synth(summary="Get a thing, changed")

    deployments = [set(_resources(t, "AWS::ApiGateway::Deployment")) for t in (before, same, after)]
    assert deployments[0] == deployments[1]
    assert deployments[0] != deployments[2]


def test_a_token_in_the_definition_fails_the_synth():
    app = aws_cdk.App()
    stack = aws_cdk.Stack(app, "Api", env=aws_cdk.Environment(account=ACCOUNT, region=REGION))
    deploy_time_value = aws_ssm.StringParameter.value_for_string_parameter(stack, "/some/arn")

    with pytest.raises(ValueError, match="CDK token"):
        openapi_s3.asset_api_definition(stack, "api", _document(deploy_time_value, "arn:aws:iam::1:role/r"))


def test_an_unknown_pseudo_parameter_fails_the_synth():
    with pytest.raises(ValueError, match=r"\$\{AWS::StackName\}"):
        openapi_s3.literal_document({"x": "${AWS::StackName}"}, ACCOUNT, REGION)


def test_an_unnamed_function_has_no_literal_arn():
    stack = aws_cdk.Stack(aws_cdk.App(), "Api", env=aws_cdk.Environment(account=ACCOUNT, region=REGION))
    function = aws_lambda.Function(
        stack,
        "Handler",
        runtime=aws_lambda.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=aws_lambda.Code.from_inline("def handler(event, context): pass"),
    )

    with pytest.raises(ValueError, match="function name"):
        openapi_s3.function_arn(function, ACCOUNT, REGION)


def test_the_authorizer_arn_names_the_authorization_stacks_function():
    app_config = config.AppConfig(
        environment="dev",
        account=ACCOUNT,
        region=REGION,
        web_app_account=ACCOUNT,
        component_name="provisioning",
        environment_config=config.env_config["dev"],
        component_specific={},
    )
    authorizer_name = app_config.format_resource_name_with_component(
        constants.AUTH_BC_NAME, authorization_app_stack.Entrypoint.AUTHORIZER_REQUEST_EVENTS
    )

    assert openapi_s3.authorizer_arn(app_config) == (
        f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{authorizer_name}:{constants.LAMBDA_ALIAS_NAME}"
    )
