"""The portal reports Provisioning's authoritative settings and the actual synthesized schedules."""

import copy
import json
from types import SimpleNamespace

import aws_cdk
import pytest
from aws_cdk import aws_lambda
from aws_cdk.assertions import Template

from app.provisioning.domain.model.workbench_lifecycle import PlatformLifecycleDefaults
from infra import config
from infra.backend.provisioning_app_stack import ProvisioningAppStack
from infra.backend.workbench_lifecycle_runtime_config import lifecycle_environment


@pytest.mark.parametrize("idle_enabled", [False, True])
def test_portal_environment_uses_provisioning_config(idle_enabled):
    lifecycle = copy.deepcopy(config.provisioning_app_config["prod"]["workbench-lifecycle"])
    lifecycle.update(idleStopMinutes=125, nightlyStopTime="23:17", timezone="Europe/Stockholm")
    lifecycle["idleStop"]["enabled"] = idle_enabled
    env = lifecycle_environment(lifecycle)
    assert (
        json.loads(env["WORKBENCH_LIFECYCLE_DEFAULTS"])
        == PlatformLifecycleDefaults.model_validate(lifecycle).model_dump()
    )
    assert json.loads(env["WORKBENCH_LIFECYCLE_DEPLOYMENT"]) == {
        "idleStopEnabled": idle_enabled,
        "nightlyStopScheduleEnabled": True,
    }


def test_nightly_schedule_runs_for_project_overrides_while_idle_stop_remains_disabled():
    app = aws_cdk.App()
    stack = aws_cdk.Stack(app, "LifecycleSchedules")
    fn = aws_lambda.Function(
        stack,
        "Jobs",
        runtime=aws_lambda.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=aws_lambda.Code.from_inline("def handler(event, context): pass"),
    )
    stack._scheduled_jobs_handler_name = "jobs"
    stack._backend_app = SimpleNamespace(app_entries_functions={"jobs": fn})
    app_config = config.AppConfig(
        account="111111111111",
        region="eu-north-1",
        environment="prod",
        web_app_account="111111111111",
        component_name="provisioning",
        environment_config=config.env_config["prod"],
        component_specific=config.provisioning_app_config["prod"],
    )
    assert app_config.component_specific["workbench-lifecycle"]["nightlyStop"] is False
    ProvisioningAppStack._workbench_lifecycle_schedules(stack, app_config)
    template = Template.from_stack(stack)
    template.has_resource_properties(
        "AWS::Scheduler::Schedule",
        {
            "State": "ENABLED",
            "ScheduleExpression": "cron(0 21 * * ? *)",
            "ScheduleExpressionTimezone": "UTC",
            "Target": {
                "Input": json.dumps(
                    {"jobName": "WorkbenchLifecycleJob", "parameters": {"action": "nightly-stop", "dryRun": False}}
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": app_config.format_resource_name("workbench-idle-stop"),
            "State": "DISABLED",
        },
    )
