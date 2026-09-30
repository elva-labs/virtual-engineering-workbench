"""Read group object IDs only from an authenticated identity profile."""

import json
from urllib.parse import unquote
from uuid import UUID

GROUPS_CLAIM = "custom:entra_groups"


def group_ids(profile: dict) -> list[str]:
    value = profile.get(GROUPS_CLAIM)
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            # Cognito flattens a multi-valued OIDC attribute into [value,value].
            text = value.strip()
            value = (
                text[1:-1].split(",")
                if text.startswith("[") and text.endswith("]")
                else [text]
            )
    if not isinstance(value, list):
        return []
    try:
        return (
            sorted(
                {
                    str(UUID(unquote(item.strip())))
                    for item in value
                    if isinstance(item, str)
                }
            )
            if all(isinstance(item, str) for item in value)
            else []
        )
    except (ValueError, AttributeError):
        return []


def effective_roles(direct_assignments, group_assignments) -> dict[str, list[str]]:
    roles: dict[str, set[str]] = {}
    for assignment in [*direct_assignments, *group_assignments]:
        if getattr(assignment, "isDeleted", False):
            continue
        roles.setdefault(assignment.projectId, set()).update(assignment.roles)
    return {project_id: sorted(values) for project_id, values in roles.items()}
