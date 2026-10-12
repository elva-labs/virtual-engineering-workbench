"""Run the delivered collector against Linux command fixtures; unknown observations never become idle."""

import json
import os
import shutil
import subprocess

import pytest

from infra.tests.test_workbench_agents import AGENT, _stub


def _run_agent(tmp_path, dcv='case "$1" in list-sessions) ;; list-connections) ;; esac', processes="", who="true"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    # Isolate PATH so optional DCV absence is reproducible on any test machine.
    for name in ("awk", "cut", "cat", "date", "grep"):
        (bin_dir / name).symlink_to(shutil.which(name))
    _stub(bin_dir, "curl", 'case "$*" in *api/token*) echo token ;; *instance-id*) echo i-test ;; *) exit 22 ;; esac')
    if dcv is not None:
        _stub(bin_dir, "dcv", dcv)
    _stub(bin_dir, "who", who)
    # Simulate the complete argv strings Linux pgrep -f inspects. Fail if the collector
    # tries matching the truncated comm field (-x without -f).
    _stub(
        bin_dir, "pgrep", '[ "$1" = "-fc" ] || exit 2\ngrep -Ec -- "$2" <<\'PROCESSES\'\n' + processes + "\nPROCESSES"
    )
    _stub(bin_dir, "nproc", "echo 4")
    _stub(bin_dir, "aws", f'printf "%s\\0" "$@" >> "{calls}"')
    _stub(bin_dir, "shutdown", f'printf "shutdown\\0" >> "{calls}"')
    conf = tmp_path / "autostop.conf"
    conf.write_text("ACTION=report\n")
    state = tmp_path / "idle"
    state.write_text("60")
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.10 0.20 0.30 1/100 1\n")
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "VEW_AUTOSTOP_CONF": str(conf),
        "VEW_IDLE_STATE": str(state),
        "VEW_LOADAVG": str(loadavg),
    }
    # Stubs use /usr/bin/env bash, so provide bash even with the isolated PATH.
    (bin_dir / "bash").symlink_to(shutil.which("bash"))
    run = subprocess.run([shutil.which("bash"), str(AGENT)], env=env, capture_output=True, text=True, check=True)
    args = calls.read_text().split("\0")
    data = json.loads(args[args.index("--metric-data") + 1])
    assert "shutdown" not in args
    return {point["MetricName"]: point["Value"] for point in data}, run.stdout, state.read_text().strip()


def test_active_ssm_only_session_resets_idle_without_counting_runcommand_or_probes(tmp_path):
    processes = "\n".join(
        [
            "/usr/bin/ssm-session-worker session-id channel",
            "/usr/bin/ssm-document-worker command-id",  # RunCommand
            "/usr/bin/ssm-session-worker-helper",  # a different executable
            "pgrep -fc ^([^[:space:]]*/)?ssm-session-worker([[:space:]]|$)",
            "/bin/bash -c pgrep -fc ssm-session-worker",
        ]
    )
    metrics, output, state = _run_agent(tmp_path, processes=processes)
    assert metrics["InteractiveSessions"] == 1
    assert metrics["IdleMinutes"] == 0
    assert state == "0"
    assert "busy: interactive sessions: 1" in output


def test_runcommand_and_probes_are_not_interactive_sessions(tmp_path):
    metrics, _, _ = _run_agent(
        tmp_path,
        processes="/usr/bin/ssm-document-worker\n/bin/bash -c ssm-session-worker\npgrep -fc ssm-session-worker",
    )
    assert metrics["InteractiveSessions"] == 0
    assert metrics["IdleMinutes"] == 65


@pytest.mark.parametrize(
    "dcv",
    [
        "exit 1",
        'case "$1" in list-sessions) echo "Session: s1 (owner:u type:console)" ;; list-connections) exit 1 ;; esac',
        'case "$1" in list-sessions) echo "unexpected session format" ;; esac',
    ],
)
def test_failed_or_unrecognized_dcv_observation_does_not_publish_healthy_zero(tmp_path, dcv):
    metrics, output, state = _run_agent(tmp_path, dcv=dcv)
    assert "DcvConnections" not in metrics
    assert "IdleMinutes" not in metrics
    assert metrics["InteractiveSessions"] == 0
    assert metrics["LoadPerCpu"] == 0.05
    assert "dcv=unknown dcv_probe=failed" in output
    assert state == "0"


@pytest.mark.parametrize("dcv,probe", [(None, "absent"), ("exit 0", "ok")])
def test_absent_optional_dcv_and_successful_no_session_listing_are_distinguishable(tmp_path, dcv, probe):
    metrics, output, state = _run_agent(tmp_path, dcv=dcv)
    assert metrics["DcvConnections"] == 0
    assert metrics["IdleMinutes"] == 65
    assert f"dcv_probe={probe}" in output
    assert state == "65"


@pytest.mark.parametrize("session", ["s1", "'s1'"])
def test_documented_and_older_quoted_session_ids_detect_clients(tmp_path, session):
    dcv = f"""case "$1" in
      list-sessions) echo "Session: {session} (owner:u type:console name:'session')" ;;
      list-connections) [ "$2" = s1 ] || exit 2; echo "Connection: client" ;;
    esac"""
    metrics, _, state = _run_agent(tmp_path, dcv=dcv)
    assert metrics["DcvConnections"] == 1
    assert metrics["IdleMinutes"] == 0
    assert state == "0"


def test_successful_no_connections_keeps_a_known_zero(tmp_path):
    dcv = 'case "$1" in list-sessions) echo "Session: s1 (owner:u type:console)" ;; list-connections) exit 0 ;; esac'
    metrics, _, _ = _run_agent(tmp_path, dcv=dcv)
    assert metrics["DcvConnections"] == 0
    assert metrics["IdleMinutes"] == 65
