import logging
from unittest.mock import Mock

from app.projects.domain.group_membership_service import GroupMembershipService
from app.projects.entrypoints.group_sign_in import bootstrapper


def test_bootstrap_registers_the_existing_membership_handlers(monkeypatch):
    for key in (
        "TABLE_NAME",
        "GSI_NAME_INVERTED_PK",
        "GSI_NAME_AWS_ACCOUNTS",
        "GSI_NAME_ENTITIES",
        "GSI_NAME_QPK",
        "GSI_NAME_QSK",
        "COGNITO_USER_POOL_ID",
        "DOMAIN_EVENT_BUS_ARN",
        "BOUNDED_CONTEXT",
    ):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(bootstrapper.boto_logger, "loggable_session", Mock(return_value=Mock()))
    dependencies = bootstrapper.bootstrap(logging.getLogger(__name__))
    assert isinstance(dependencies.group_membership_service, GroupMembershipService)
    assert set(dependencies.group_membership_service._commands._command_handlers) == {
        "AssignUserCommand",
        "ReAssignUserCommand",
        "UnAssignUserCommand",
    }
