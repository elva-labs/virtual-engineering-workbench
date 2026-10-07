from typing import Optional

from pydantic import Field

from app.shared.adapters.unit_of_work_v2 import unit_of_work


class ProjectSettingsPrimaryKey(unit_of_work.PrimaryKey):
    projectId: str = Field(..., title="ProjectId")


class ProjectSettings(unit_of_work.Entity):
    """Project settings the authorizer needs, synced from the Projects BC (ProjectUpdated events).

    A project without an item has the defaults: remote support enabled, managed in the portal.
    """

    projectId: str = Field(..., title="ProjectId")
    # Whether SUPPORT may reach the project's workbenches (Cedar resource attribute remoteSupportEnabled).
    remoteSupportEnabled: bool = Field(True, title="RemoteSupportEnabled")
    # The external tool that owns the project's configuration (for example "terraform"): the user APIs
    # refuse configuration changes. None = the portal.
    managedBy: Optional[str] = Field(None, title="ManagedBy")
    managedSource: Optional[str] = Field(None, title="ManagedSource")
    # "workbench-only" limits the program's members other than platform admins to their workbenches
    # . None or "full" = upstream's portal.
    experience: Optional[str] = Field(None, title="Experience")
