import logging
from unittest import mock

import assertpy
import boto3
import moto
import pytest

from app.projects.adapters.services import cognito_user_directory_service

TEST_REGION = "us-east-1"


@pytest.fixture
def cognito_client():
    with moto.mock_aws():
        yield boto3.client("cognito-idp", region_name=TEST_REGION)


@pytest.fixture
def user_pool(cognito_client):
    pool = cognito_client.create_user_pool(
        PoolName="test-pool",
        Schema=[
            {
                "Name": "user_tid",
                "AttributeDataType": "String",
                "Mutable": True,
                "Required": False,
            },
            {
                "Name": "email",
                "AttributeDataType": "String",
                "Mutable": True,
                "Required": True,
            },
            {
                "Name": "entra_groups",
                "AttributeDataType": "String",
                "Mutable": True,
                "Required": False,
            },
        ],
        UsernameAttributes=["email"],
    )
    return pool["UserPool"]


def _create_user(cognito_client, user_pool_id, email, user_tid, **names):
    cognito_client.admin_create_user(
        UserPoolId=user_pool_id,
        Username=email,
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
            {"Name": "custom:user_tid", "Value": user_tid},
            *({"Name": name, "Value": value} for name, value in names.items()),
        ],
        MessageAction="SUPPRESS",
    )


def _service(cognito_client, user_pool):
    return cognito_user_directory_service.CognitoUserDirectoryService(
        cognito_client=cognito_client,
        user_pool_id=user_pool["Id"],
        logger=logging.getLogger("test"),
    )


def test_returns_email_when_user_tid_matches(cognito_client, user_pool):
    # Arrange
    _create_user(cognito_client, user_pool["Id"], "alice@example.com", "ALICE")
    _create_user(cognito_client, user_pool["Id"], "bob@example.com", "BOB")

    service = cognito_user_directory_service.CognitoUserDirectoryService(
        cognito_client=cognito_client,
        user_pool_id=user_pool["Id"],
        logger=logging.getLogger("test"),
    )

    # Act & Assert
    assertpy.assert_that(service.get_user_email("ALICE")).is_equal_to("alice@example.com")
    assertpy.assert_that(service.get_user_email("BOB")).is_equal_to("bob@example.com")


def test_returns_none_when_user_tid_not_found(cognito_client, user_pool):
    # Arrange
    _create_user(cognito_client, user_pool["Id"], "alice@example.com", "ALICE")

    service = cognito_user_directory_service.CognitoUserDirectoryService(
        cognito_client=cognito_client,
        user_pool_id=user_pool["Id"],
        logger=logging.getLogger("test"),
    )

    # Act & Assert
    assertpy.assert_that(service.get_user_email("DOES_NOT_EXIST")).is_none()


def test_returns_none_for_empty_user_tid(cognito_client, user_pool):
    service = cognito_user_directory_service.CognitoUserDirectoryService(
        cognito_client=cognito_client,
        user_pool_id=user_pool["Id"],
        logger=logging.getLogger("test"),
    )

    assertpy.assert_that(service.get_user_email("")).is_none()


def test_match_ignores_the_case_of_user_tid(cognito_client, user_pool):
    # Entra ID object ids arrive lowercase in custom:user_tid while VEW keys users by the
    # uppercased id (authorizer_steps); both spellings must find the same user.
    _create_user(cognito_client, user_pool["Id"], "alice@example.com", "4c4863be-d953-47a2-bf45-7e0a4ef7ac13")

    service = _service(cognito_client, user_pool)

    assertpy.assert_that(service.get_user_email("4C4863BE-D953-47A2-BF45-7E0A4EF7AC13")).is_equal_to(
        "alice@example.com"
    )
    assertpy.assert_that(service.get_user_email("4c4863be-d953-47a2-bf45-7e0a4ef7ac13")).is_equal_to(
        "alice@example.com"
    )


def test_profile_has_given_and_family_name(cognito_client, user_pool):
    _create_user(
        cognito_client, user_pool["Id"], "tobias@example.com", "abc-1", given_name="Tobias", family_name="Edvardsson"
    )

    profile = _service(cognito_client, user_pool).get_user_profile("ABC-1")

    assertpy.assert_that(profile.email).is_equal_to("tobias@example.com")
    assertpy.assert_that(profile.display_name).is_equal_to("Tobias Edvardsson")


def test_profile_display_name_falls_back_to_the_email_local_part(cognito_client, user_pool):
    _create_user(cognito_client, user_pool["Id"], "first.last@example.com", "u-2")

    assertpy.assert_that(_service(cognito_client, user_pool).get_user_profile("U-2").display_name).is_equal_to(
        "first.last"
    )


def test_requests_only_attributes_the_pool_exposes():
    # ListUsers fails as a whole if AttributesToGet names an attribute the pool does not expose
    # (the live pool rejects the standard "name" attribute).
    client = mock.MagicMock()
    client.get_paginator.return_value.paginate.return_value = []
    service = cognito_user_directory_service.CognitoUserDirectoryService(
        cognito_client=client, user_pool_id="pool", logger=logging.getLogger("test")
    )

    service.get_user_profiles(["A"])

    requested = client.get_paginator.return_value.paginate.call_args.kwargs["AttributesToGet"]
    assertpy.assert_that(requested).is_equal_to(["custom:user_tid", "email", "given_name", "family_name"])


def test_get_user_profiles_returns_known_users_keyed_by_vew_user_id(cognito_client, user_pool):
    _create_user(cognito_client, user_pool["Id"], "alice@example.com", "alice-id")
    _create_user(cognito_client, user_pool["Id"], "bob@example.com", "bob-id")

    profiles = _service(cognito_client, user_pool).get_user_profiles(["ALICE-ID", "bob-id", "UNKNOWN"])

    assertpy.assert_that(sorted(profiles)).is_equal_to(["ALICE-ID", "BOB-ID"])
    assertpy.assert_that(profiles["BOB-ID"].email).is_equal_to("bob@example.com")


def test_returns_none_and_logs_when_cognito_raises():
    # Simulate an unavailable IdP — the adapter must degrade gracefully so
    # user onboarding is not blocked by a transient Cognito outage.
    broken_client = mock.MagicMock()
    broken_client.get_paginator.side_effect = RuntimeError("cognito is down")
    logger = mock.MagicMock(spec=logging.Logger)

    service = cognito_user_directory_service.CognitoUserDirectoryService(
        cognito_client=broken_client,
        user_pool_id="us-east-1_fake",
        logger=logger,
    )

    assertpy.assert_that(service.get_user_email("ALICE")).is_none()
    assertpy.assert_that(service.get_user_profiles(["ALICE", "BOB"])).is_equal_to({})
    assertpy.assert_that(logger.exception.call_count).is_equal_to(2)


def test_get_user_groups_claim_returns_the_stored_groups_of_the_user(cognito_client, user_pool):
    _create_user(
        cognito_client, user_pool["Id"], "a@example.com", "4c4863be-aaaa", **{"custom:entra_groups": "[g-1,g-2]"}
    )
    _create_user(cognito_client, user_pool["Id"], "b@example.com", "5d5974cf-bbbb")
    service = _service(cognito_client, user_pool)

    assertpy.assert_that(service.get_user_groups_claim("4C4863BE-AAAA")).is_equal_to("[g-1,g-2]")
    assertpy.assert_that(service.get_user_groups_claim("5D5974CF-BBBB")).is_none()
    assertpy.assert_that(service.get_user_groups_claim("UNKNOWN")).is_none()


def test_get_user_groups_claim_on_failure_grants_nothing(user_pool):
    broken = mock.Mock()
    broken.get_paginator.side_effect = RuntimeError("boom")
    service = cognito_user_directory_service.CognitoUserDirectoryService(
        cognito_client=broken, user_pool_id=user_pool["Id"], logger=logging.getLogger("test")
    )

    assertpy.assert_that(service.get_user_groups_claim("4C4863BE-AAAA")).is_none()
