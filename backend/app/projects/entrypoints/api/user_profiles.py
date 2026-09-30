"""Display details for listed project members.

Assignments store the user's email and display name when they are created. Assignments made before
that (or while the identity provider did not know the user yet) lack them, which leaves members
shown by Entra object id only. List endpoints fill the gaps from the identity provider on read - one
directory scan per request, and only when something is missing - without writing: a user's email
and name belong to Entra ID, the assignment only caches them for display.
"""

from typing import Sequence, TypeVar

from app.projects.domain.model import project_assignment
from app.projects.domain.ports import user_directory_service

AssignmentT = TypeVar("AssignmentT", bound=project_assignment.Assignment)


def with_profiles(
    assignments: Sequence[AssignmentT],
    directory: user_directory_service.UserDirectoryService,
) -> Sequence[AssignmentT]:
    incomplete = [a for a in assignments if not a.userEmail or not a.userDisplayName]
    if not incomplete:
        return assignments

    profiles = directory.get_user_profiles(a.userId for a in incomplete)
    for assignment in incomplete:
        profile = profiles.get(user_directory_service.canonical_user_id(assignment.userId))
        if not profile:
            continue
        assignment.userEmail = assignment.userEmail or profile.email
        assignment.userDisplayName = assignment.userDisplayName or profile.display_name
    return assignments
