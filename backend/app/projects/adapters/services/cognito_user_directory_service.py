import logging
from typing import TYPE_CHECKING, Iterable, Optional

from app.shared.identity import entra_groups
from app.projects.domain.ports import user_directory_service

if TYPE_CHECKING:
    from mypy_boto3_cognito_idp import CognitoIdentityProviderClient

_USER_TID_ATTRIBUTE = "custom:user_tid"
# The Entra group ids of the last sign-in (app.shared.identity.entra_groups).
_GROUPS_ATTRIBUTE = entra_groups.GROUPS_CLAIM
_EMAIL_ATTRIBUTE = "email"
_GIVEN_NAME_ATTRIBUTE = "given_name"
_FAMILY_NAME_ATTRIBUTE = "family_name"
# Only attributes the user pool exposes: ListUsers rejects the whole request ("Input fails to
# satisfy the constraints") if AttributesToGet names one it does not, e.g. the standard ``name``
# attribute, which the pool may not map from the IdP.
_ATTRIBUTES_TO_GET = [_USER_TID_ATTRIBUTE, _EMAIL_ATTRIBUTE, _GIVEN_NAME_ATTRIBUTE, _FAMILY_NAME_ATTRIBUTE]


def _display_name(attributes: dict[str, Optional[str]]) -> Optional[str]:
    """Given and family name; else the local part of the email."""
    full_name = " ".join(
        part for part in (attributes.get(_GIVEN_NAME_ATTRIBUTE), attributes.get(_FAMILY_NAME_ATTRIBUTE)) if part
    )
    if full_name:
        return full_name
    email = attributes.get(_EMAIL_ATTRIBUTE)
    return email.split("@")[0] if email else None


class CognitoUserDirectoryService(user_directory_service.UserDirectoryService):
    """Cognito-backed implementation of ``UserDirectoryService``.

    Looks up the Cognito users whose ``custom:user_tid`` matches the supplied
    user ids - case-insensitively: Cognito keeps the claim as the IdP sent it
    (a lowercase Entra object id), VEW keys users by the uppercased value.

    Cognito does not support server-side filtering on custom attributes via
    ``ListUsers --filter``, so this adapter paginates through the pool once per
    call and matches client-side. That is acceptable for VEW's user-pool scale
    (hundreds to low thousands); if a deployment outgrows it, cache this
    lookup or maintain a reverse index on the ``user_tid``.

    Any failure degrades gracefully to "unknown": a missing email or name must
    never block user onboarding.
    """

    _PAGE_SIZE = 60  # Cognito ListUsers hard cap

    def __init__(
        self,
        cognito_client: "CognitoIdentityProviderClient",
        user_pool_id: str,
        logger: logging.Logger,
    ):
        self._client = cognito_client
        self._user_pool_id = user_pool_id
        self._logger = logger

    def get_user_profiles(self, user_tids: Iterable[str]) -> dict[str, user_directory_service.UserProfile]:
        wanted = {user_directory_service.canonical_user_id(tid) for tid in user_tids if tid}
        if not wanted:
            return {}

        found: dict[str, user_directory_service.UserProfile] = {}
        try:
            paginator = self._client.get_paginator("list_users")
            pages = paginator.paginate(
                UserPoolId=self._user_pool_id,
                AttributesToGet=_ATTRIBUTES_TO_GET,
                PaginationConfig={"PageSize": self._PAGE_SIZE},
            )

            for page in pages:
                for cognito_user in page.get("Users", []):
                    attributes = {attr["Name"]: attr.get("Value") for attr in cognito_user.get("Attributes", [])}
                    user_tid = attributes.get(_USER_TID_ATTRIBUTE)
                    if not user_tid:
                        continue
                    user_id = user_directory_service.canonical_user_id(user_tid)
                    if user_id in wanted and user_id not in found:
                        found[user_id] = user_directory_service.UserProfile(
                            email=attributes.get(_EMAIL_ATTRIBUTE),
                            display_name=_display_name(attributes),
                        )
                if len(found) == len(wanted):
                    break

            missing = wanted - found.keys()
            if missing:
                self._logger.info(
                    "No Cognito user found for user_tid(s)=%s in pool=%s",
                    ",".join(sorted(missing)),
                    self._user_pool_id,
                )
            return found

        except Exception:
            # Lookup is best-effort — we don't want identity-provider issues
            # to block user onboarding or listing members. Log and return what
            # is known so far; callers treat the rest as "unknown".
            self._logger.exception(
                "Failed to look up Cognito users for user_tid(s)=%s in pool=%s",
                ",".join(sorted(wanted)),
                self._user_pool_id,
            )
            return {}

    def get_user_groups_claim(self, user_tid: str) -> Optional[str]:
        # ListUsers rejects the groups attribute in AttributesToGet ("Input fails to satisfy the constraints"),
        # so this asks for every attribute and picks the user by custom:user_tid.
        wanted = user_directory_service.canonical_user_id(user_tid) if user_tid else None
        if not wanted:
            return None
        try:
            paginator = self._client.get_paginator("list_users")
            for page in paginator.paginate(
                UserPoolId=self._user_pool_id, PaginationConfig={"PageSize": self._PAGE_SIZE}
            ):
                for cognito_user in page.get("Users", []):
                    attributes = {attr["Name"]: attr.get("Value") for attr in cognito_user.get("Attributes", [])}
                    tid = attributes.get(_USER_TID_ATTRIBUTE)
                    if tid and user_directory_service.canonical_user_id(tid) == wanted:
                        return attributes.get(_GROUPS_ATTRIBUTE)
            return None
        except Exception:
            self._logger.exception("Failed to look up the groups of user_tid=%s in pool=%s", wanted, self._user_pool_id)
            return None
