from unittest import mock

from assertpy import assert_that

from app.shared.middleware.authorization import AuthType, require_auth_context


def test_iam_auth():
    # ARRANGE
    app = mock.MagicMock()
    app.current_event = {
        "requestContext": {
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": "123456789012",
                "cognitoIdentityId": None,
                "caller": "uid:caller-session-name",
                "sourceIp": "0.0.0.0",
                "principalOrgId": "o-123",
                "accessKey": "access-key",
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": "arn:aws:sts::001234567890:assumed-role/RoleName/role-session-name",
                "userAgent": "node",
                "user": "uid:user-session-name",
            },
        }
    }
    app.context = {}
    app.append_context = lambda **kwargs: app.context.update(kwargs)

    def next_middleware(resolver):
        return None

    # ACT
    require_auth_context(app, next_middleware)

    # ASSERT
    assert_that(app.context["user_principal"].user_name).is_equal_to("USER-SESSION-NAME")
    assert_that(app.context["user_principal"].auth_type).is_equal_to(AuthType.ServiceIAM)
    assert_that(app.context["user_principal"].account_id).is_equal_to("123456789012")


def test_cognito_user_jwt_auth():
    # ARRANGE
    app = mock.MagicMock()
    app.current_event = {
        "requestContext": {
            "authorizer": {
                "userName": "test-user",
                "userEmail": "test@example.com",
                "stages": '["dev"]',
                "userRoles": '["ADMIN"]',
                "userDomains": '["domain1"]',
            }
        }
    }
    app.context = {}
    app.append_context = lambda **kwargs: app.context.update(kwargs)

    def next_middleware(resolver):
        return None

    # ACT
    require_auth_context(app, next_middleware)

    # ASSERT
    assert_that(app.context["user_principal"].user_name).is_equal_to("test-user")
    assert_that(app.context["user_principal"].auth_type).is_equal_to(AuthType.CognitoUserJWT)
    assert_that(app.context["user_principal"].user_email).is_equal_to("test@example.com")


def test_cognito_service_jwt_auth():
    # ARRANGE
    app = mock.MagicMock()
    app.current_event = {
        "requestContext": {
            "authorizer": {
                "claims": {
                    "client_id": "service-client-id",
                }
            }
        }
    }
    app.context = {}
    app.append_context = lambda **kwargs: app.context.update(kwargs)

    def next_middleware(resolver):
        return None

    # ACT
    require_auth_context(app, next_middleware)

    # ASSERT
    assert_that(app.context["user_principal"].user_name).is_equal_to("service-client-id")
    assert_that(app.context["user_principal"].auth_type).is_equal_to(AuthType.CognitoServiceJWT)


def _user_principal(extra: dict):
    app = mock.MagicMock()
    app.current_event = {
        "requestContext": {
            "authorizer": {
                "userName": "test-user",
                "userEmail": "admin@example.com",
                "stages": "[]",
                "userRoles": '["ADMIN"]',
                "userDomains": "[]",
                **extra,
            }
        }
    }
    app.context = {}
    app.append_context = lambda **kwargs: app.context.update(kwargs)
    require_auth_context(app, lambda resolver: None)
    return app.context["user_principal"]


def test_a_platform_admins_writes_are_recorded_as_such():
    # The authorizer marks ADMIN that came only from a platform-admin group.
    principal = _user_principal({"platformAdminAccess": "true"})

    assert_that(principal.platform_admin_access).is_true()
    assert_that(principal.actor).is_equal_to("admin@example.com (platform admin)")


def test_an_own_admin_role_is_recorded_as_the_user():
    for extra in ({}, {"platformAdminAccess": ""}):
        principal = _user_principal(extra)

        assert_that(principal.platform_admin_access).is_false()
        assert_that(principal.actor).is_equal_to("admin@example.com")
