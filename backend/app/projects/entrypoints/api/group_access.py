"""Additions to the Entra group access, for the projects API.

The projects API computes a user's effective roles as the union of direct assignments and the Entra group
assignments of the groups in the sign-in (app.shared.identity.entra_groups). On top of that:

- platform-admin groups (authorization/projects config "platform-admin-groups") make their members
  ADMIN on every project, without a group assignment per project;
- server-side callers without a sign-in token (a launch, a support session) resolve the same roles
  from the user's Cognito record (the groups attribute written at every sign-in).
"""

import json
import os
import typing

from app.projects.domain.model import project_assignment
from app.shared.identity import entra_groups


def platform_admin_groups() -> set[str]:
    """Entra groups whose members are ADMIN on every project (config "platform-admin-groups")."""
    return {g.lower() for g in json.loads(os.environ.get("PLATFORM_ADMIN_GROUPS", "[]"))}


def groups_from_claim(value: typing.Any) -> list[str]:
    """Group ids from a stored groups value, with the authorizer's parsing rules."""
    return entra_groups.group_ids({entra_groups.GROUPS_CLAIM: value}) if value else []


def is_platform_admin(user_groups: typing.Iterable[str], admin_groups: set[str]) -> bool:
    return bool({g.lower() for g in user_groups} & admin_groups)


def effective_assignment(
    user_id: str,
    project_id: str,
    direct: project_assignment.Assignment | None,
    group_assignments: list,
    platform_admin: bool,
) -> project_assignment.Assignment | None:
    """The user's roles on one project: direct roles, their groups' grants and platform ADMIN."""
    roles = entra_groups.effective_roles(
        [direct] if direct else [], [a for a in group_assignments if a.projectId == project_id]
    ).get(project_id, [])
    if platform_admin and project_assignment.Role.ADMIN.value not in roles:
        roles = sorted({*roles, project_assignment.Role.ADMIN.value})
    if not roles:
        return direct
    if direct:
        return direct.model_copy(update={"roles": roles})
    return project_assignment.Assignment(userId=user_id, projectId=project_id, roles=roles)
