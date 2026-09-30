"""A project's workbench stop policy.

Workbenches stop (idle stop, nightly stop, weekend stop) and never start by themselves. The policy is
set through the S2S API (vew_project_workbench_lifecycle); unset fields fall back to the deployment's
defaults. The allow* flags are the admins' switches for what the project's users may change on their
own workbenches, within the given bounds; the Provisioning BC computes the effective values per
workbench.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The agent checks every 5 minutes; below 10 minutes a workbench would stop between two checks of a
# user who just stepped away.
MIN_IDLE_MINUTES = 10
MAX_IDLE_MINUTES = 1440


class WorkbenchLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bypasses idle, nightly and weekend stops (the program accepts the cost).
    alwaysOn: bool = Field(False, title="AlwaysOn")
    # None = platform default.
    idleStopMinutes: Optional[int] = Field(None, ge=MIN_IDLE_MINUTES, le=MAX_IDLE_MINUTES, title="IdleStopMinutes")
    nightlyStop: Optional[bool] = Field(None, title="NightlyStop")
    weekendStop: Optional[bool] = Field(None, title="WeekendStop")
    # Admin switches: what the program's users may change on their own workbenches.
    allowUserDisableNightlyStop: bool = Field(False, title="AllowUserDisableNightlyStop")
    allowUserIdleTimeout: bool = Field(False, title="AllowUserIdleTimeout")
    userIdleTimeoutMinMinutes: int = Field(
        30, ge=MIN_IDLE_MINUTES, le=MAX_IDLE_MINUTES, title="UserIdleTimeoutMinMinutes"
    )
    userIdleTimeoutMaxMinutes: int = Field(
        480, ge=MIN_IDLE_MINUTES, le=MAX_IDLE_MINUTES, title="UserIdleTimeoutMaxMinutes"
    )

    @model_validator(mode="after")
    def _bounds(self) -> "WorkbenchLifecycle":
        if self.userIdleTimeoutMinMinutes > self.userIdleTimeoutMaxMinutes:
            raise ValueError("userIdleTimeoutMinMinutes must not exceed userIdleTimeoutMaxMinutes")
        return self
