"""Projects exposes the actual Provisioning deployment configuration in its policy API."""

import json

from app.provisioning.domain.model.workbench_lifecycle import PlatformLifecycleDefaults

# The job evaluates each project's policy; the schedule must run even when the platform's
# default is off, otherwise a project's explicit nightlyStop=True could never take effect.
NIGHTLY_STOP_SCHEDULE_ENABLED = True


def lifecycle_environment(lifecycle: dict) -> dict[str, str]:
    return {
        "WORKBENCH_LIFECYCLE_DEFAULTS": PlatformLifecycleDefaults.model_validate(lifecycle).model_dump_json(),
        "WORKBENCH_LIFECYCLE_DEPLOYMENT": json.dumps(
            {
                "idleStopEnabled": bool(lifecycle.get("idleStop", {}).get("enabled", False)),
                "nightlyStopScheduleEnabled": NIGHTLY_STOP_SCHEDULE_ENABLED,
            }
        ),
    }
