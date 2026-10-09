import logging
from types import SimpleNamespace
from unittest import mock

from app.packaging.entrypoints.s2s_api import bootstrapper, config
from app.shared.adapters.message_bus import event_bridge_message_bus
from app.shared.logging import boto_logger


def test_s2s_safe_logger_never_forwards_debug_payloads():
    logger = mock.Mock()
    safe_logger = bootstrapper.S2SSafeLogger(logger)

    safe_logger.debug({"Payload": {"componentVersionDefinition": "secret"}})
    safe_logger.info({"Command": "CreateComponentVersionCommand"})

    logger.debug.assert_not_called()
    logger.info.assert_called_once_with({"Command": "CreateComponentVersionCommand"})


def test_s2s_safe_logger_redacts_error_details():
    logger = mock.Mock()
    safe_logger = bootstrapper.S2SSafeLogger(logger)

    safe_logger.error(RuntimeError("TOP_SECRET_DEFINITION"))

    assert "TOP_SECRET_DEFINITION" not in str(logger.error.call_args)


def test_s2s_safe_logger_keeps_boto_debug_logging_metadata_only():
    raw_key = "4fce2226-7b23-4f1b-bfd1-70a396dbb32d"
    definition_secret = "SENSITIVE_COMPONENT_DEFINITION"
    logger = mock.Mock(log_level=logging.DEBUG)
    safe_logger = bootstrapper.S2SSafeLogger(logger)
    session = SimpleNamespace(events=mock.Mock())

    boto_logger.loggable_session(session, safe_logger)
    callback = session.events.register.call_args.args[1]
    callback(
        {
            "Item": {"idempotencyKey": raw_key},
            "Key": {"SK": f"IDEMPOTENCY#{raw_key}"},
            "definition": definition_secret,
        },
        "provide-client-params.dynamodb.PutItem",
    )

    rendered = str(logger.mock_calls)
    assert raw_key not in rendered
    assert definition_secret not in rendered
    assert "dynamodb" in rendered
    assert "PutItem" in rendered
    logger.debug.assert_not_called()


def test_s2s_safe_logger_keeps_event_bridge_debug_logging_metadata_only():
    raw_key = "4fce2226-7b23-4f1b-bfd1-70a396dbb32d"
    definition_secret = "SENSITIVE_COMPONENT_DEFINITION"
    logger = mock.Mock(log_level=logging.DEBUG)
    events_api = mock.Mock()
    message = mock.Mock(event_name="ComponentVersionCreated")
    message.model_dump_json.return_value = f'{{"idempotencyKey":"{raw_key}","definition":"{definition_secret}"}}'
    message.model_dump.return_value = {
        "idempotencyKey": raw_key,
        "definition": definition_secret,
    }

    event_bridge_message_bus.EventBridgeMessageBus(
        events_api=events_api,
        event_bus_name="domain-events",
        bounded_context_name="packaging",
        logger=bootstrapper.S2SSafeLogger(logger),
    ).publish(message)

    rendered = str(logger.mock_calls)
    assert raw_key not in rendered
    assert definition_secret not in rendered
    assert "ComponentVersionCreated" in rendered
    logger.debug.assert_not_called()


def test_bootstrapper_wires_access_commands_and_component_queries(monkeypatch):
    session = mock.Mock()
    loggable_session = mock.Mock(return_value=session)
    event_bus = mock.Mock()
    event_bus_factory = mock.Mock(return_value=event_bus)
    monkeypatch.setattr(bootstrapper.boto_logger, "loggable_session", loggable_session)
    monkeypatch.setattr(bootstrapper.event_bridge_message_bus, "EventBridgeMessageBus", event_bus_factory)
    monkeypatch.setattr(bootstrapper.parameters, "get_parameter", mock.Mock(return_value="{}"))

    result = bootstrapper.bootstrap(config.AppConfig(), mock.Mock())

    assert result.project_access_service is not None
    assert result.component_domain_qry_srv is not None
    assert result.component_version_domain_qry_srv is not None
    assert result.component_version_qry_srv is not None
    assert result.recipe_domain_qry_srv is not None
    assert result.recipe_version_domain_qry_srv is not None
    assert result.idempotency_service._client is session.resource.return_value.meta.client
    safe_logger = loggable_session.call_args.args[1]
    assert isinstance(safe_logger, bootstrapper.S2SSafeLogger)
    assert event_bus_factory.call_args.kwargs["logger"] is safe_logger
    session.client.assert_any_call("ssm", region_name="eu-west-1")
