from unittest import mock

import assertpy

from app.projects.domain.model import project_assignment
from app.projects.domain.ports import user_directory_service
from app.projects.entrypoints.api import user_profiles


def _assignment(user_id, email=None, name=None):
    return project_assignment.Assignment(
        userId=user_id, projectId="proj-1", roles=["PLATFORM_USER"], userEmail=email, userDisplayName=name
    )


def _directory(profiles):
    directory = mock.create_autospec(spec=user_directory_service.UserDirectoryService, instance=True)
    directory.get_user_profiles.return_value = profiles
    return directory


def test_fills_missing_email_and_name_from_the_directory():
    assignments = [_assignment("4C4863BE-D953-47A2-BF45-7E0A4EF7AC13")]
    directory = _directory(
        {
            "4C4863BE-D953-47A2-BF45-7E0A4EF7AC13": user_directory_service.UserProfile(
                email="tobias@example.com", display_name="Tobias Edvardsson"
            )
        }
    )

    result = user_profiles.with_profiles(assignments, directory)

    assertpy.assert_that(result[0].userEmail).is_equal_to("tobias@example.com")
    assertpy.assert_that(result[0].userDisplayName).is_equal_to("Tobias Edvardsson")


def test_keeps_stored_values_and_only_asks_for_incomplete_assignments():
    complete = _assignment("A", email="a@example.com", name="A Person")
    no_name = _assignment("B", email="b@example.com")
    directory = _directory(
        {"B": user_directory_service.UserProfile(email="other@example.com", display_name="B Person")}
    )

    user_profiles.with_profiles([complete, no_name], directory)

    assertpy.assert_that(list(directory.get_user_profiles.call_args.args[0])).is_equal_to(["B"])
    assertpy.assert_that(no_name.userEmail).is_equal_to("b@example.com")
    assertpy.assert_that(no_name.userDisplayName).is_equal_to("B Person")
    assertpy.assert_that(complete.userDisplayName).is_equal_to("A Person")


def test_does_not_call_the_directory_when_nothing_is_missing():
    directory = _directory({})

    user_profiles.with_profiles([_assignment("A", email="a@example.com", name="A")], directory)

    directory.get_user_profiles.assert_not_called()


def test_unknown_users_stay_as_they_are():
    assignment = _assignment("UNKNOWN")

    user_profiles.with_profiles([assignment], _directory({}))

    assertpy.assert_that(assignment.userEmail).is_none()
    assertpy.assert_that(assignment.userDisplayName).is_none()
