from unittest import mock

from app.packaging.entrypoints.s2s_api import bootstrapper, config


def test_s2s_command_logger_never_forwards_debug_payloads():
    logger = mock.Mock()
    safe_logger = bootstrapper.S2SCommandLogger(logger)

    safe_logger.debug({"Payload": {"componentVersionDefinition": "secret"}})
    safe_logger.info({"Command": "CreateComponentVersionCommand"})

    logger.debug.assert_not_called()
    logger.info.assert_called_once_with({"Command": "CreateComponentVersionCommand"})


def test_s2s_command_logger_redacts_error_details():
    logger = mock.Mock()
    safe_logger = bootstrapper.S2SCommandLogger(logger)

    safe_logger.error(RuntimeError("TOP_SECRET_DEFINITION"))

    assert "TOP_SECRET_DEFINITION" not in str(logger.error.call_args)


def test_bootstrapper_wires_access_commands_and_component_queries(monkeypatch):
    session = mock.Mock()
    monkeypatch.setattr(bootstrapper.boto_logger, "loggable_session", mock.Mock(return_value=session))
    monkeypatch.setattr(bootstrapper.parameters, "get_parameter", mock.Mock(return_value="{}"))

    result = bootstrapper.bootstrap(config.AppConfig(), mock.Mock())

    assert result.project_access_service is not None
    assert result.component_domain_qry_srv is not None
    assert result.component_version_domain_qry_srv is not None
    assert result.component_version_qry_srv is not None
    assert result.recipe_domain_qry_srv is not None
    assert result.recipe_version_domain_qry_srv is not None
    assert result.idempotency_service._client is session.resource.return_value.meta.client
    session.client.assert_any_call("ssm", region_name="eu-west-1")
