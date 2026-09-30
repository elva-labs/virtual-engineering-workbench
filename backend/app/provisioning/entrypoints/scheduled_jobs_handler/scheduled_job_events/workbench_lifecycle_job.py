from typing import Literal

from pydantic import BaseModel


class WorkbenchLifecycleJob(BaseModel):
    """nightly-stop: stop running workbenches that keep the nightly stop (EventBridge Scheduler, 21:00
    the deployment's time zone). reconcile: write the effective inactivity timeout to vew:autostop. dryRun only
    reports what would happen."""

    action: Literal["nightly-stop", "reconcile"]
    dryRun: bool = False
