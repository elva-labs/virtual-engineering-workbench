"""The workbench role cleanup removes foreign policies only, and never blocks a stack delete."""

import os
import pathlib

import assertpy
import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-north-1")

from infra import constants  # noqa: E402
from infra.usecase.resources import workbench_role_cleanup as cleanup  # noqa: E402

TEMPLATES = pathlib.Path(__file__).parents[1] / "backend" / "resources" / "product_publishing_app_stack" / "templates"
QUICK_SETUP = "arn:aws:iam::aws:policy/AWSQuickSetupPatchPolicyBaselineAccess"
SSM_CORE = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"


class _NoSuchEntity(Exception):
    pass


class _FakeIam:
    class exceptions:  # noqa: N801 - mirrors boto3's client.exceptions
        NoSuchEntityException = _NoSuchEntity

    def __init__(self, managed, inline, missing=False):
        self.managed, self.inline, self.missing = list(managed), list(inline), missing
        self.calls = []

    def get_paginator(self, name):
        fake = self

        class _Paginator:
            def paginate(self, RoleName):  # noqa: N803 - boto3 keyword
                if fake.missing:
                    raise _NoSuchEntity(RoleName)
                if name == "list_attached_role_policies":
                    return [{"AttachedPolicies": [{"PolicyArn": arn} for arn in fake.managed]}]
                return [{"PolicyNames": list(fake.inline)}]

        return _Paginator()

    def detach_role_policy(self, RoleName, PolicyArn):  # noqa: N803
        self.calls.append(("detach", PolicyArn))

    def delete_role_policy(self, RoleName, PolicyName):  # noqa: N803
        self.calls.append(("delete", PolicyName))


def test_only_policies_the_template_does_not_declare_are_removed():
    iam = _FakeIam(managed=[SSM_CORE, QUICK_SETUP], inline=["AllowCloudWatchLogs", "VewDebugLogRetention"])

    removed = cleanup.strip_foreign_policies("SC-1-pp-x-InstanceRole-y", {SSM_CORE}, {"AllowCloudWatchLogs"}, iam)

    assertpy.assert_that(removed).is_equal_to([QUICK_SETUP, "VewDebugLogRetention"])
    assertpy.assert_that(iam.calls).is_equal_to([("detach", QUICK_SETUP), ("delete", "VewDebugLogRetention")])


def test_a_role_that_is_already_gone_is_not_an_error():
    iam = _FakeIam(managed=[], inline=[], missing=True)

    assertpy.assert_that(cleanup.strip_foreign_policies("gone", set(), set(), iam)).is_empty()


class _Context:
    log_stream_name = "stream"


def _event(request_type):
    return {
        "RequestType": request_type,
        "ResponseURL": "https://example.invalid/response",
        "StackId": "stack",
        "RequestId": "req",
        "LogicalResourceId": "InstanceRoleCleanup",
        "ResourceProperties": {
            "RoleName": "SC-1-pp-x-InstanceRole-y",
            "KeepManagedPolicyArns": [SSM_CORE],
            "KeepInlinePolicyNames": [],
        },
    }


@pytest.mark.parametrize("request_type", ["Create", "Update"])
def test_create_and_update_touch_nothing(monkeypatch, request_type):
    sent = []
    monkeypatch.setattr(cleanup, "_send", lambda event, context, status, reason="": sent.append(status))
    monkeypatch.setattr(cleanup, "strip_foreign_policies", lambda *args: pytest.fail("must not run"))

    cleanup.handler(_event(request_type), _Context())

    assertpy.assert_that(sent).is_equal_to(["SUCCESS"])


def test_a_failing_cleanup_still_reports_success_so_the_delete_goes_on(monkeypatch):
    """CloudFormation then fails on the role with its own, clearer error instead of hanging."""
    sent = []
    monkeypatch.setattr(cleanup, "_send", lambda event, context, status, reason="": sent.append((status, reason)))

    def boom(*args):
        raise RuntimeError("AccessDenied")

    monkeypatch.setattr(cleanup, "strip_foreign_policies", boom)

    cleanup.handler(_event("Delete"), _Context())

    assertpy.assert_that(sent[0][0]).is_equal_to("SUCCESS")
    assertpy.assert_that(sent[0][1]).contains("AccessDenied")


def test_the_function_fits_inline_lambda_code():
    source = pathlib.Path(cleanup.__file__).read_text()

    assertpy.assert_that(len(source.encode())).is_less_than(4096)  # CloudFormation ZipFile limit


@pytest.mark.parametrize("name", ["workbench-template.yml", "virtual-target-template.yml"])
def test_templates_resolve_the_parameter_the_spoke_writes(name):
    template = (TEMPLATES / name).read_text()

    assertpy.assert_that(template).contains(f"Default: {constants.WORKBENCH_ROLE_CLEANUP_FUNCTION_ARN_PARAMETER}")
    # The launch constraint role may invoke exactly this function (product_publishing_enablement_app_stack).
    assertpy.assert_that(constants.WORKBENCH_ROLE_CLEANUP_FUNCTION).is_not_empty()
