"""The idle agent delivered by SSM: the install script writes exactly the files in git, the
agent reports its signals as valid metric data, and it never stops a workbench in report mode."""

import json
import os
import pathlib
import stat
import subprocess

import assertpy

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-north-1")

from infra.usecase.resources import workbench_agents  # noqa: E402

AGENT = workbench_agents.HERE / "idle" / "vew-idle-agent.sh"


def _stub(bin_dir: pathlib.Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_install_script_writes_the_files_from_git(tmp_path):
    root = tmp_path / "root"
    script = "\n".join(workbench_agents.install_script())
    for _, target, _ in workbench_agents.IDLE_AGENT_FILES:
        script = script.replace(f"'{target}'", f"'{root}{target}'")
    script = script.replace("install -d -m 0755 /etc/vew /var/lib/vew /var/log/vew /usr/local/sbin", "")
    script = script.replace("> /etc/vew/agents.version", f"> '{root}/etc/vew/agents.version'")
    script = script.replace("$(cat /etc/vew/agents.version)", f"$(cat '{root}/etc/vew/agents.version')")
    for directory in ("etc/vew", "etc/systemd/system", "usr/local/sbin"):
        (root / directory).mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "systemctl", f'echo "$@" >> "{tmp_path}/systemctl.log"')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    # sh, not bash: SSM's aws:runShellScript runs the script with /bin/sh (dash on Ubuntu).
    first = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, check=True)
    second = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, check=True)

    for source, target, _ in workbench_agents.IDLE_AGENT_FILES:
        assertpy.assert_that((root / target.lstrip("/")).read_bytes()).is_equal_to(
            (workbench_agents.HERE / source).read_bytes()
        )
    assertpy.assert_that(first.stdout).contains("changed=1")
    assertpy.assert_that(second.stdout).contains("changed=0")
    assertpy.assert_that((tmp_path / "systemctl.log").read_text()).contains("enable --now vew-idle-agent.timer")


def test_the_delivered_config_only_reports():
    config = (workbench_agents.HERE / "idle" / "autostop.conf").read_text()

    assertpy.assert_that(config).contains("\nACTION=report\n")


def test_the_agent_publishes_every_signal_and_does_not_stop(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _stub(bin_dir, "curl", 'case "$*" in *api/token*) echo token ;; *instance-id*) echo i-123 ;; *) exit 22 ;; esac')
    _stub(bin_dir, "dcv", 'case "$1" in list-sessions) echo "Session: \'s1\' (owner:u)";; list-connections) ;; esac')
    _stub(bin_dir, "who", "true")
    _stub(bin_dir, "pgrep", "echo 0; exit 1")
    _stub(bin_dir, "nproc", "echo 4")
    _stub(bin_dir, "aws", f'printf "%s\\0" "$@" >> "{calls}"')
    _stub(bin_dir, "shutdown", f'printf "shutdown\\0" >> "{calls}"')
    conf = tmp_path / "autostop.conf"
    conf.write_text(
        (workbench_agents.HERE / "idle" / "autostop.conf").read_text().replace("IDLE_MINUTES=60", "IDLE_MINUTES=5")
    )
    state = tmp_path / "idle"
    state.write_text("60")
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.10 0.20 0.30 1/100 1\n")  # 0.20 on 4 CPUs = 0.05 per CPU
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VEW_AUTOSTOP_CONF": str(conf),
        "VEW_IDLE_STATE": str(state),
        "VEW_LOADAVG": str(loadavg),
    }

    run = subprocess.run(["bash", str(AGENT)], env=env, capture_output=True, text=True, check=True)

    args = calls.read_text().split("\0")
    data = json.loads(args[args.index("--metric-data") + 1])
    assertpy.assert_that([m["MetricName"] for m in data]).is_equal_to(
        ["DcvConnections", "InteractiveSessions", "LoadPerCpu", "IdleMinutes"]
    )
    values = {m["MetricName"]: m["Value"] for m in data}
    assertpy.assert_that(values).contains_entry(
        {"DcvConnections": 0}, {"InteractiveSessions": 0}, {"LoadPerCpu": 0.05}, {"IdleMinutes": 65}
    )
    assertpy.assert_that(data[0]["Dimensions"]).is_equal_to([{"Name": "InstanceId", "Value": "i-123"}])
    assertpy.assert_that(run.stdout).contains("idle=65m action=report")
    assertpy.assert_that(calls.read_text()).does_not_contain("shutdown")


def test_the_idle_stop_is_off_by_default():
    from infra import config as infra_config

    config = infra_config.provisioning_app_config["prod"]["workbench-lifecycle"]["idleStop"]

    assertpy.assert_that(config).contains_entry({"enabled": False}, {"dryRun": False}, {"minCoverage": 0.8})
    assertpy.assert_that(config["signals"]).is_equal_to(["dcvConnections", "interactiveSessions", "load"])


def test_the_spoke_stack_delivers_the_agents_by_association():
    import aws_cdk
    from aws_cdk import assertions

    from infra import config
    from infra.usecase import provisioning_enablement_stack

    app = aws_cdk.App()
    app_config = config.AppConfig(
        account="111111111111",
        web_app_account="111111111111",
        environment="prod",
        region="eu-north-1",
        component_name="provisioning-enablement",
        environment_config=config.env_config["prod"],
        component_specific={**config.provisioning_enablement_app_config["prod"], "workbench-agents-enabled": True},
    )
    stack = provisioning_enablement_stack.ProvisioningEnablementStack(
        app,
        "ProvisioningEnablementStack",
        app_config=app_config,
        env=aws_cdk.Environment(account="111111111111", region="eu-north-1"),
        web_application_account="222222222222",
        web_application_region="eu-north-1",
    )
    template = assertions.Template.from_stack(stack)

    document = next(iter(template.find_resources("AWS::SSM::Document").values()))["Properties"]
    assertpy.assert_that(document["UpdateMethod"]).is_equal_to("NewVersion")
    commands = document["Content"]["mainSteps"][0]["inputs"]["runCommand"]
    assertpy.assert_that(commands).is_equal_to(workbench_agents.install_script())
    template.has_resource_properties(
        "AWS::SSM::Association",
        {
            "DocumentVersion": "$LATEST",
            "Targets": [{"Key": "tag:vew:provisionedProduct:productType", "Values": ["WORKBENCH"]}],
            "ApplyOnlyAtCronInterval": False,
        },
    )


def test_the_spoke_stack_has_no_association_unless_enabled():
    import aws_cdk
    from aws_cdk import assertions

    from infra import config
    from infra.usecase import provisioning_enablement_stack

    app = aws_cdk.App()
    stack = provisioning_enablement_stack.ProvisioningEnablementStack(
        app,
        "ProvisioningEnablementStack",
        app_config=config.AppConfig(
            account="111111111111",
            web_app_account="111111111111",
            environment="prod",
            region="eu-north-1",
            component_name="provisioning-enablement",
            environment_config=config.env_config["prod"],
            component_specific=config.provisioning_enablement_app_config["prod"],
        ),
        env=aws_cdk.Environment(account="111111111111", region="eu-north-1"),
        web_application_account="222222222222",
        web_application_region="eu-north-1",
    )

    assertions.Template.from_stack(stack).resource_count_is("AWS::SSM::Association", 0)
