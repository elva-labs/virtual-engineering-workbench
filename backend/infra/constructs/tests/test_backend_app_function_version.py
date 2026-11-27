"""Every function is called through its live alias, which points at CDK's currentVersion. These tests pin how a
change reaches that alias."""

import json
import pathlib

import aws_cdk
from aws_cdk import aws_lambda
from aws_cdk.assertions import Template

from infra.constructs import backend_app_function

_CDK_JSON = pathlib.Path(__file__).resolve().parents[3] / "cdk-backend.json"

# The Lambda flags that shape currentVersion's hash, per backend_app_function.VERSION_EPOCH.
VERSION_HASH_FLAGS = {"v2": {"@aws-cdk/aws-lambda:recognizeLayerVersion": True}}


def _project_context() -> dict:
    # deploy.sh copies cdk-backend.json to cdk.json. The CDK CLI reads only its "context" object as context;
    # feature flags at the top level are ignored.
    return json.loads(_CDK_JSON.read_text())["context"]


def _alias_version(tmp_path: pathlib.Path, layer_content: str, context: dict) -> str:
    layer_dir = tmp_path / f"layer-{len(list(tmp_path.iterdir()))}"
    (layer_dir / "python").mkdir(parents=True)
    (layer_dir / "python" / "shared.py").write_text(layer_content)

    # Bundling is skipped: the function's code asset doesn't matter here, only its layer does.
    app = aws_cdk.App(context={**context, "aws:cdk:bundling-stacks": []})
    stack = aws_cdk.Stack(
        app, "FunctionVersionTest", env=aws_cdk.Environment(account="111111111111", region="eu-west-1")
    )
    layer = aws_lambda.LayerVersion(stack, "SharedLayer", code=aws_lambda.Code.from_asset(str(layer_dir)))
    backend_app_function.BackendAppFunction(
        stack,
        "Function",
        entry="app/authorization/entrypoints/api_gateway_authorizer_request_event_handler",
        app_root="app",
        lambda_root="app/authorization",
        layers=[layer],
        function_name="test-function",
    )

    aliases = [
        r["Properties"]
        for r in Template.from_stack(stack).to_json()["Resources"].values()
        if r["Type"] == "AWS::Lambda::Alias"
    ]
    assert len(aliases) == 1
    return aliases[0]["FunctionVersion"]["Fn::GetAtt"][0]


def test_cdk_backend_json_turns_on_recognize_layer_version_as_context():
    assert _project_context().get("@aws-cdk/aws-lambda:recognizeLayerVersion") is True


def test_a_layer_change_publishes_a_new_version_behind_the_alias(tmp_path):
    """Without @aws-cdk/aws-lambda:recognizeLayerVersion, currentVersion's hash covers the function's own properties
    but not its layers: a deploy that changes only a layer (shared_app_libraries) updates $LATEST and leaves the alias
    on the older version with the older layer."""
    context = _project_context()

    before = _alias_version(tmp_path, "VALUE = 1\n", context)
    after = _alias_version(tmp_path, "VALUE = 2\n", context)

    assert before != after


def test_without_the_flag_a_layer_change_leaves_the_alias_where_it_was(tmp_path):
    # The failure the flag fixes, kept as a control: same layer change, flag off.
    before = _alias_version(tmp_path, "VALUE = 1\n", {})
    after = _alias_version(tmp_path, "VALUE = 2\n", {})

    assert before == after


def test_a_change_to_the_version_hash_flags_bumps_the_version_epoch():
    """Turning on recognizeLayerVersion changes every function's version hash but not the functions themselves. Lambda
    refuses to publish an unchanged $LATEST again (AlreadyExists) and the stack rolls back. A new VERSION_EPOCH changes
    every function's description along with the flag, so each publishes its new version."""
    lambda_flags = {k: v for k, v in _project_context().items() if k.startswith("@aws-cdk/aws-lambda:")}

    assert lambda_flags == VERSION_HASH_FLAGS[backend_app_function.VERSION_EPOCH], (
        "a Lambda flag in cdk-backend.json's context changed: bump VERSION_EPOCH in backend_app_function.py and"
        " record it here"
    )


def test_the_version_epoch_is_in_every_function_description(tmp_path):
    app = aws_cdk.App(context={**_project_context(), "aws:cdk:bundling-stacks": []})
    stack = aws_cdk.Stack(app, "EpochTest")
    backend_app_function.BackendAppFunction(
        stack,
        "Function",
        entry="app/authorization/entrypoints/api_gateway_authorizer_request_event_handler",
        app_root="app",
        lambda_root="app/authorization",
        layers=[],
        function_name="test-function",
    )

    functions = Template.from_stack(stack).find_resources("AWS::Lambda::Function")
    descriptions = [f["Properties"]["Description"] for f in functions.values()]
    assert descriptions and all(d.endswith(f" {backend_app_function.VERSION_EPOCH}") for d in descriptions)
