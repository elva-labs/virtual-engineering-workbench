"""Group grants require a Cognito-linked identity, not just a writable attribute."""

import json
import os
from urllib.parse import unquote

GROUPS_ATTRIBUTE = "custom:oidc_groups"


def provider_name() -> str:
    """An empty provider disables group grants, including stored memberships."""
    return os.environ.get("OIDC_GROUPS_PROVIDER_NAME", "")


def trusted_group_ids(profile: dict) -> list[str]:
    provider = provider_name()
    if not provider:
        return []
    identities = profile.get("identities")
    if isinstance(identities, str):
        try:
            identities = json.loads(identities)
        except ValueError:
            return []
    if not isinstance(identities, list) or not any(
        isinstance(identity, dict) and identity.get("providerName") == provider for identity in identities
    ):
        return []
    return group_ids(profile.get(GROUPS_ATTRIBUTE))


def group_ids(value) -> list[str]:
    """Read JSON arrays or Cognito's URL-encoded, flattened multi-value format.

    Group identifiers are opaque and case-sensitive. Decode flattened values
    only: JSON values are already the original strings.
    """
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            text = value
            value = (
                [unquote(item) for item in text[1:-1].split(",")]
                if text.startswith("[") and text.endswith("]")
                else [text]
            )
        else:
            value = parsed if isinstance(parsed, list) else [parsed if isinstance(parsed, str) else value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return sorted({item for item in value if item})


def effective_roles(direct_assignments, group_assignments) -> dict[str, list[str]]:
    roles: dict[str, set[str]] = {}
    for assignment in [*direct_assignments, *group_assignments]:
        if getattr(assignment, "isDeleted", False):
            continue
        roles.setdefault(assignment.projectId, set()).update(assignment.roles)
    return {project_id: sorted(values) for project_id, values in roles.items()}
