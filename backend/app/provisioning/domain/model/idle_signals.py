"""The idle stop is decided in the hub from the signals the workbench agent reports.

The workbench's agent only reports signals (namespace VEW/Workbench, dimension InstanceId, every 5
minutes): connected DCV clients, interactive SSH/SSM sessions and the load per CPU. The provisioning
scheduled job reads them from the workbench account's CloudWatch and stops a workbench through VEW's
own stop path once every counted signal stayed quiet for the effective inactivity timeout. The rules
live here and in the deployment config (infra/config.py workbench-lifecycle idleStop), so they change
with a deploy, not with a new image.

Missing or stale required data never stops a workbench. Sustained loss of any required signal is
reported (and alarmed on), even when the other signals still arrive.
"""

import math
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

METRICS_NAMESPACE = "VEW/Workbench"
DIMENSION = "InstanceId"
PERIOD_SECONDS = 300

# Metric names the agent publishes, by the signal they carry (config: idleStop.signals).
SIGNAL_METRICS = {
    "dcvConnections": "DcvConnections",
    "interactiveSessions": "InteractiveSessions",
    "load": "LoadPerCpu",
}
Signal = Literal["dcvConnections", "interactiveSessions", "load"]


class IdleStopConfig(BaseModel):
    """The deployment's idle stop rules, handed to the Lambda as WORKBENCH_IDLE_STOP."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Log and count what would be stopped, stop nothing.
    dryRun: bool = False
    signals: list[Signal] = Field(
        default_factory=lambda: ["dcvConnections", "interactiveSessions", "load"], min_length=1
    )
    # Busy while the 5-minute load average per CPU reaches this.
    loadPerCpuThreshold: float = 0.15
    # Share of the 5-minute periods in the window that must have data before a workbench counts as idle.
    minCoverage: float = Field(0.8, gt=0, le=1)
    # Every required signal must also have a recent observation; quiet history is not live evidence.
    maxSampleAgeMinutes: int = Field(10, ge=1, le=30)
    # Never stop a workbench that started less than this ago (boot, first login).
    graceMinutesAfterStart: int = 15
    # Running this long without a required signal raises IdleSignalMissing (and never stops).
    missingSignalAlarmMinutes: int = 120


Point = tuple[datetime, float]


class IdleDecision(BaseModel):
    action: Literal["stop", "keep", "missing"]
    reason: str
    # Minutes covered by quiet data (0 when busy or unknown).
    quietMinutes: int = 0


def lookback_minutes(idle_minutes: int, config: IdleStopConfig) -> int:
    """How far back the job reads: the timeout window, or the missing-signal window if longer."""
    return max(idle_minutes, config.missingSignalAlarmMinutes)


def _busy(signal: str, value: float, config: IdleStopConfig) -> bool:
    if signal == "load":
        return value >= config.loadPerCpuThreshold
    return value > 0


def _missing_required_signals(signals: dict[str, list[Point]], now: datetime, config: IdleStopConfig) -> list[str]:
    alarm_start = now - timedelta(minutes=config.missingSignalAlarmMinutes)
    return [
        signal for signal in config.signals if not any(alarm_start <= ts <= now for ts, _ in signals.get(signal, []))
    ]


def _window_keep_reason(
    signal: str, window: list[Point], expected: int, now: datetime, config: IdleStopConfig
) -> str | None:
    if any(_busy(signal, value, config) for _, value in window):
        return f"busy ({signal})"
    needed = max(1, math.ceil(expected * config.minCoverage))
    if len(window) < needed:
        return f"not enough data ({signal}: {len(window)}/{expected})"
    if now - max(ts for ts, _ in window) > timedelta(minutes=config.maxSampleAgeMinutes):
        return f"stale idle signal ({signal})"
    return None


def decide(
    signals: dict[str, list[Point]],
    idle_minutes: int,
    now: datetime,
    started_at: datetime | None,
    config: IdleStopConfig,
) -> IdleDecision:
    """signals: the counted signals' 5-minute data points over lookback_minutes()."""
    running_for = (now - started_at) if started_at else None
    if running_for is not None and running_for < timedelta(minutes=config.graceMinutesAfterStart):
        return IdleDecision(action="keep", reason="started recently")

    missing_signals = _missing_required_signals(signals, now, config)
    if missing_signals:
        long_enough = running_for is None or running_for >= timedelta(minutes=config.missingSignalAlarmMinutes)
        missing = ", ".join(missing_signals)
        if long_enough:
            return IdleDecision(action="missing", reason=f"missing required idle signals ({missing})")
        return IdleDecision(action="keep", reason=f"no idle signals yet ({missing})")

    window_start = now - timedelta(minutes=idle_minutes)
    if started_at and started_at > window_start:
        return IdleDecision(action="keep", reason="running for less than the inactivity timeout")

    expected = idle_minutes * 60 // PERIOD_SECONDS
    for signal in config.signals:
        window = [(ts, v) for ts, v in signals.get(signal, []) if window_start <= ts <= now]
        reason = _window_keep_reason(signal, window, expected, now, config)
        if reason:
            return IdleDecision(action="keep", reason=reason)
    return IdleDecision(
        action="stop",
        reason=f"Stopped after {idle_minutes} minutes without activity",
        quietMinutes=idle_minutes,
    )
