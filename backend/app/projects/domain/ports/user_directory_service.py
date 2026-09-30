import abc
from typing import Iterable, Optional

from pydantic import BaseModel, Field


def canonical_user_id(user_tid: str) -> str:
    """The VEW user id for an identity provider ``user_tid``.

    Mirrors the authorizer (``authorizer_steps``): the part before ``@``, uppercased. With Entra ID
    the claim is the object id (``oid``), which Cognito stores lowercase while VEW keys users by
    the uppercased value - comparing the raw values never matches.
    """
    return user_tid.split("@")[0].upper()


class UserProfile(BaseModel):
    """What the identity provider knows about a user, for display."""

    email: Optional[str] = Field(None, title="Email")
    display_name: Optional[str] = Field(None, title="DisplayName")


class UserDirectoryService(abc.ABC):
    """Abstract port for looking up attributes of the identity provider's users.

    Decouples the domain from the concrete identity provider (Cognito today;
    another IdP later) so handlers can enrich domain entities — e.g. populate
    ``userEmail`` and ``userDisplayName`` on a project assignment — without taking
    a dependency on a specific SDK.
    """

    @abc.abstractmethod
    def get_user_profiles(self, user_tids: Iterable[str]) -> dict[str, UserProfile]:
        """Return the profiles of the users identified by ``user_tids``, keyed by VEW user id.

        ``user_tid`` is the identifier VEW stores on the IdP user record (``custom:user_tid`` in
        Cognito). Matching is on :func:`canonical_user_id`, so casing does not matter.

        Users the IdP does not know are absent from the result. Any failure returns an empty
        dict: callers MUST treat a missing profile as "unknown" rather than as an error - a blank
        name is recoverable, an unavailable IdP must not block user onboarding.
        """

    def get_user_groups_claim(self, user_tid: str) -> Optional[str]:
        """The raw groups value (``custom:entra_groups``) on the user's IdP record, written at every sign-in from the
        ID token's groups claim; ``None`` if unknown or on any failure (a lookup problem can only ever
        grant less)."""
        return None

    def get_user_profile(self, user_tid: str) -> Optional[UserProfile]:
        """The profile of one user, or ``None`` if unknown (see :meth:`get_user_profiles`)."""
        if not user_tid:
            return None
        return self.get_user_profiles([user_tid]).get(canonical_user_id(user_tid))

    def get_user_email(self, user_tid: str) -> Optional[str]:
        """The email of one user, or ``None`` if unknown."""
        profile = self.get_user_profile(user_tid)
        return profile.email if profile else None
