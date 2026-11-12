from unittest.mock import Mock

import pytest

from app.projects.entrypoints.group_sign_in import handler


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", "CorporateLogin")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "pool")
    dependencies = Mock()
    monkeypatch.setattr(handler, "dependencies", dependencies)
    cognito = Mock()
    cognito.admin_get_user.return_value = {
        "Enabled": True,
        "UserAttributes": [{"Name": "custom:user_tid", "Value": "member"}],
    }
    monkeypatch.setattr(handler.boto3, "client", Mock(return_value=cognito))
    return dependencies, cognito


@pytest.mark.parametrize("source", ["TokenGeneration_HostedAuth", "TokenGeneration_Authentication"])
def test_sign_in_reads_latest_cognito_profile(setup, source):
    dependencies, cognito = setup
    handler.handler({"userPoolId": "pool", "userName": "username", "triggerSource": source}, None)
    cognito.admin_get_user.assert_called_once_with(UserPoolId="pool", Username="username")
    dependencies.group_membership_service.sign_in.assert_called_once_with("member", {"custom:user_tid": "member"})


def test_refresh_does_not_restore_removed_members(setup):
    dependencies, cognito = setup
    handler.handler({"userPoolId": "pool", "triggerSource": "TokenGeneration_RefreshTokens"}, None)
    dependencies.group_membership_service.sign_in.assert_not_called()
    cognito.admin_get_user.assert_not_called()


def test_feature_off_skips_cognito_and_memberships(setup, monkeypatch):
    dependencies, cognito = setup
    monkeypatch.setenv("OIDC_GROUPS_PROVIDER_NAME", "")
    handler.handler({}, None)
    dependencies.group_membership_service.sign_in.assert_not_called()
    cognito.admin_get_user.assert_not_called()


def test_different_user_pool_rejected(setup):
    with pytest.raises(ValueError, match="Unexpected user pool"):
        handler.handler({"userPoolId": "other"}, None)


def test_disabled_account_is_not_assigned(setup):
    dependencies, cognito = setup
    cognito.admin_get_user.return_value["Enabled"] = False
    handler.handler({"userPoolId": "pool", "userName": "username", "triggerSource": "TokenGeneration_HostedAuth"}, None)
    dependencies.group_membership_service.sign_in.assert_not_called()
