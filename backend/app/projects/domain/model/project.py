import random
import string
from typing import Optional

from pydantic import Field

from app.projects.domain.model import workbench_lifecycle
from app.shared.adapters.unit_of_work_v2 import unit_of_work

# The managing tool's name, e.g. "terraform".
MANAGED_BY_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"


def generate_project_id() -> str:
    return "proj-" + "".join((random.choice(string.ascii_lowercase + string.digits) for x in range(5)))


class ProjectPrimaryKey(unit_of_work.PrimaryKey):
    projectId: str = Field(..., title="ProjectId")


# What the program's members get in the portal. "full" is upstream's portal; in a
# "workbench-only" program everyone but platform admins sees only their workbenches, consumes PROD
# only and launches workbenches only.
EXPERIENCE_FULL = "full"
EXPERIENCE_WORKBENCH_ONLY = "workbench-only"
EXPERIENCES = (EXPERIENCE_FULL, EXPERIENCE_WORKBENCH_ONLY)
EXPERIENCE_PATTERN = r"^(full|workbench-only)$"


class Project(unit_of_work.Entity):
    projectId: str = Field(default_factory=generate_project_id, title="ProjectId")
    projectName: str = Field(..., title="ProjectName")
    projectDescription: Optional[str] = Field(None, title="ProjectDescription")
    isActive: bool = Field(..., title="IsActive")
    createDate: Optional[str] = Field(None, title="CreateDate")
    lastUpdateDate: Optional[str] = Field(None, title="LastUpdateDate")
    # Remote support by SUPPORT staff on this project's workbenches; projects with sensitive work switch
    # it off. Absent on older items, which means enabled.
    remoteSupportEnabled: bool = Field(True, title="RemoteSupportEnabled")
    # Set when an external tool (for example a Terraform configuration) owns the project's
    # configuration: the user APIs then refuse configuration changes. None = managed in the portal.
    managedBy: Optional[str] = Field(None, title="ManagedBy", pattern=MANAGED_BY_PATTERN)
    # Where the configuration lives, shown to users whose change is refused.
    managedSource: Optional[str] = Field(None, title="ManagedSource", max_length=256)
    # The project's workbench stop policy and what its users may change; None = the deployment's
    # defaults, users may change nothing.
    workbenchLifecycle: Optional[workbench_lifecycle.WorkbenchLifecycle] = Field(None, title="WorkbenchLifecycle")
    # Absent on older items, which means "full".
    experience: str = Field(EXPERIENCE_FULL, title="Experience", pattern=EXPERIENCE_PATTERN)
