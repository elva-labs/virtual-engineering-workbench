from assertpy import assert_that

from app.shared.logging import helpers

AUTH_HEADER = "Bearer a.b.c"


def test_clear_auth_headers_should_mask_credentials():
    # ARRANGE
    payload = {
        "resource": "/projects",
        "httpMethod": "GET",
        "headers": {"Authorization": AUTH_HEADER},
        "multiValueHeaders": {"Authorization": [AUTH_HEADER]},
    }

    # ACT
    response = helpers.clear_auth_headers(payload)

    # ASSERT
    assert_that(response["headers"]["Authorization"]).is_equal_to("***")
    assert_that(response["multiValueHeaders"]["Authorization"][0]).is_equal_to("***")


def test_clear_auth_headers_should_work_when_no_header_present():
    # ARRANGE
    payload = {
        "resource": "/projects",
        "httpMethod": "GET",
    }

    # ACT
    response = helpers.clear_auth_headers(payload)

    # ASSERT
    assert_that(response).is_equal_to(
        {
            "resource": "/projects",
            "httpMethod": "GET",
        }
    )


def test_clear_auth_headers_should_mask_user_info():
    # ARRANGE
    payload = {
        "resource": "/notifications",
        "httpMethod": "GET",
        "requestContext": {"authorizer": {"userEmail": "user@example.com", "userName": "testuser", "userRoles": "[]"}},
    }

    # ACT
    response = helpers.clear_auth_headers(payload)

    # ASSERT
    assert_that(response["requestContext"]["authorizer"]["userEmail"]).is_equal_to("***")
    assert_that(response["requestContext"]["authorizer"]["userName"]).is_equal_to("***")
    assert_that(response["requestContext"]["authorizer"]["userRoles"]).is_equal_to("[]")


def test_clear_auth_headers_masks_idempotency_key_case_insensitively_and_optional_body():
    payload = {
        "headers": {"idempotency-key": "secret-key"},
        "multiValueHeaders": {"IDEMPOTENCY-KEY": ["secret-key"]},
        "body": '{"componentVersionDefinition":{"secret":"value"}}',
    }

    response = helpers.clear_auth_headers(payload, mask_body=True)

    assert_that(response["headers"]["idempotency-key"]).is_equal_to("***")
    assert_that(response["multiValueHeaders"]["IDEMPOTENCY-KEY"]).is_equal_to(["***"])
    assert_that(response["body"]).is_equal_to("***")
    assert_that(payload["body"]).is_not_equal_to("***")


def test_clear_auth_headers_preserves_body_by_default():
    payload = {"body": '{"safe":"existing-api-behavior"}'}

    response = helpers.clear_auth_headers(payload)

    assert_that(response["body"]).is_equal_to(payload["body"])
