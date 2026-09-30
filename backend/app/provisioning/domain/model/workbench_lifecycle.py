"""Workbench stop rules: deployment default -> project -> user, within the project's bounds.

Decision 16: workbenches stop (idle stop, nightly stop, weekend stop) and never start by themselves.
The project's settings come from the Projects BC (vew_project_workbench_lifecycle); the admins' allow* switches decide
what a program's users may change on their own workbenches. This module computes the effective
values of one workbench and the EC2 tag the idle agent reads.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# For an idle agent in the workbench image, read through IMDS instance tags:
# "off" disables the idle stop, an integer is the inactivity timeout in minutes.
AUTOSTOP_TAG_KEY = "vew:autostop"
AUTOSTOP_OFF = "off"

Source = Literal["platform", "program", "user"]


class PlatformLifecycleDefaults(BaseModel):
    """The deployment's defaults (infra/config.py workbench-lifecycle), handed to the Lambdas as environment variables."""

    model_config = ConfigDict(extra="ignore")

    idleStopMinutes: int = 60
    nightlyStop: bool = False
    # One schedule for the platform; shown to users next to the nightly-stop switch.
    nightlyStopTime: str = "21:00"
    timezone: str = "UTC"
    weekendStop: bool = True


class ProgramLifecycle(BaseModel):
    """Read model of the Projects BC's program setting (same fields, unknown ones ignored)."""

    model_config = ConfigDict(extra="ignore")

    alwaysOn: bool = False
    idleStopMinutes: Optional[int] = None
    nightlyStop: Optional[bool] = None
    weekendStop: Optional[bool] = None
    allowUserDisableNightlyStop: bool = False
    allowUserIdleTimeout: bool = False
    userIdleTimeoutMinMinutes: int = 30
    userIdleTimeoutMaxMinutes: int = 480


class UserLifecycleSettings(BaseModel):
    """What the owner chose for their workbench; applied only while the program allows it."""

    model_config = ConfigDict(extra="forbid")

    nightlyStopDisabled: bool = Field(False, title="NightlyStopDisabled")
    # None = the program's (or platform's) timeout.
    idleTimeoutMinutes: Optional[int] = Field(None, title="IdleTimeoutMinutes")


class UserLifecyclePermissions(BaseModel):
    mayDisableNightlyStop: bool
    maySetIdleTimeout: bool
    idleTimeoutMinMinutes: int
    idleTimeoutMaxMinutes: int


class EffectiveLifecycle(BaseModel):
    alwaysOn: bool
    idleStopEnabled: bool
    idleStopMinutes: int
    nightlyStop: bool
    weekendStop: bool
    # Where each value comes from, shown to program owners and admins.
    sources: dict[str, Source]
    # A stored user choice the program no longer allows (or whose bound it no longer accepts).
    ignoredUserSettings: list[str]

    @property
    def autostop_tag(self) -> str:
        return str(self.idleStopMinutes) if self.idleStopEnabled else AUTOSTOP_OFF


class LifecycleSettingsError(ValueError):
    """A user choice the program does not allow."""


def permissions(program: ProgramLifecycle | None) -> UserLifecyclePermissions:
    program = program or ProgramLifecycle()
    return UserLifecyclePermissions(
        mayDisableNightlyStop=program.allowUserDisableNightlyStop and not program.alwaysOn,
        maySetIdleTimeout=program.allowUserIdleTimeout and not program.alwaysOn,
        idleTimeoutMinMinutes=program.userIdleTimeoutMinMinutes,
        idleTimeoutMaxMinutes=program.userIdleTimeoutMaxMinutes,
    )


def validate_user_settings(program: ProgramLifecycle | None, settings: UserLifecycleSettings) -> None:
    """Server-side guard for the portal: a user may only choose what the program allows."""
    allowed = permissions(program)
    if settings.nightlyStopDisabled and not allowed.mayDisableNightlyStop:
        raise LifecycleSettingsError("This program does not allow switching off the nightly shutdown.")
    if settings.idleTimeoutMinutes is not None:
        if not allowed.maySetIdleTimeout:
            raise LifecycleSettingsError("This program does not allow changing the inactivity timeout.")
        if not allowed.idleTimeoutMinMinutes <= settings.idleTimeoutMinutes <= allowed.idleTimeoutMaxMinutes:
            raise LifecycleSettingsError(
                f"The inactivity timeout must be between {allowed.idleTimeoutMinMinutes} and "
                f"{allowed.idleTimeoutMaxMinutes} minutes in this program."
            )


def _layered(platform_value, program_value) -> tuple:
    """The program's value where it set one, else the platform's."""
    return (program_value, "program") if program_value is not None else (platform_value, "platform")


def _idle_minutes(platform, program, user, allowed, ignored: list[str]) -> tuple[int, Source]:
    minutes, source = _layered(platform.idleStopMinutes, program.idleStopMinutes)
    if user.idleTimeoutMinutes is None:
        return minutes, source
    within = allowed.idleTimeoutMinMinutes <= user.idleTimeoutMinutes <= allowed.idleTimeoutMaxMinutes
    if allowed.maySetIdleTimeout and within:
        return user.idleTimeoutMinutes, "user"
    ignored.append("idleTimeoutMinutes")
    return minutes, source


def _nightly_stop(platform, program, user, allowed, ignored: list[str]) -> tuple[bool, Source]:
    nightly, source = _layered(platform.nightlyStop, program.nightlyStop)
    if not user.nightlyStopDisabled:
        return nightly, source
    if allowed.mayDisableNightlyStop:
        return False, "user"
    ignored.append("nightlyStopDisabled")
    return nightly, source


def effective(
    platform: PlatformLifecycleDefaults,
    program: ProgramLifecycle | None,
    user: UserLifecycleSettings | None,
) -> EffectiveLifecycle:
    program_set = program is not None
    program = program or ProgramLifecycle()
    user = user or UserLifecycleSettings()
    allowed = permissions(program)
    sources: dict[str, Source] = {}
    ignored: list[str] = []

    idle_minutes, sources["idleStopMinutes"] = _idle_minutes(platform, program, user, allowed, ignored)
    nightly, sources["nightlyStop"] = _nightly_stop(platform, program, user, allowed, ignored)
    weekend, sources["weekendStop"] = _layered(platform.weekendStop, program.weekendStop)
    sources["alwaysOn"] = "program" if program_set else "platform"

    idle_enabled = True
    if program.alwaysOn:
        # The program accepts the cost: no stop of any kind.
        idle_enabled, nightly, weekend = False, False, False
        for key in ("idleStopMinutes", "nightlyStop", "weekendStop"):
            sources[key] = "program"

    return EffectiveLifecycle(
        alwaysOn=program.alwaysOn,
        idleStopEnabled=idle_enabled,
        idleStopMinutes=idle_minutes,
        nightlyStop=nightly,
        weekendStop=weekend,
        sources=sources,
        ignoredUserSettings=ignored,
    )
