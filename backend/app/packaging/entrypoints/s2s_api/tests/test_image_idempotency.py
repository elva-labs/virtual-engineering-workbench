import importlib
import json
import re
from unittest import mock

import pytest

from app.packaging.domain.exceptions.s2s_exception import ProjectAccessDenied
from app.packaging.domain.ports.idempotency_service import IdempotencyScope, Reservation, ReservationOutcome
from app.packaging.entrypoints.s2s_api.image_client_token import derive_image_client_token
from app.packaging.entrypoints.s2s_api.model.api_model import CreateImageRequest

KEY = "b39cdd55-774d-4bc3-81a8-70f23a03c485"
PATH = "/projects/proj-1/images"
SCOPE = "clients/packaging/pipeline.execute"
IMAGE_ID = "image-fixed"


def load_handler(monkeypatch, dependencies):
    from app.packaging.entrypoints.s2s_api import bootstrapper

    monkeypatch.setattr(bootstrapper, "bootstrap", mock.Mock(return_value=dependencies))
    from app.packaging.entrypoints.s2s_api import handler

    return importlib.reload(handler)


def invoke(handler, client_event, lambda_context, *, key=KEY, pipeline_id="pipe-1", client_id="client-1", path=PATH):
    return handler.handler(
        client_event(
            "POST",
            path,
            {"pipelineId": pipeline_id},
            headers={"Idempotency-Key": key} if key is not None else None,
            client_id=client_id,
            scopes=[SCOPE],
        ),
        lambda_context,
    )


def test_image_client_token_is_versioned_scope_aware_and_bounded():
    from uuid import UUID

    scope = IdempotencyScope("client-1", "proj-1", "CREATE_IMAGE", None, UUID(KEY))
    token = derive_image_client_token(scope, IMAGE_ID)

    assert token == derive_image_client_token(scope, IMAGE_ID)
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    assert KEY not in token
    for changed in (
        IdempotencyScope("client-2", "proj-1", "CREATE_IMAGE", None, UUID(KEY)),
        IdempotencyScope("client-1", "proj-2", "CREATE_IMAGE", None, UUID(KEY)),
        IdempotencyScope("client-1", "proj-1", "CREATE_PIPELINE", None, UUID(KEY)),
    ):
        assert derive_image_client_token(changed, IMAGE_ID) != token
    assert derive_image_client_token(scope, "image-other") != token
    assert derive_image_client_token(scope, IMAGE_ID) == derive_image_client_token(
        IdempotencyScope("client-1", "proj-1", "CREATE_IMAGE", None, UUID("2676b69a-e94f-4c2a-8d39-aed5fb209546")),
        IMAGE_ID,
    )


def test_create_image_reserves_id_and_replays_without_redispatch(
    monkeypatch, mocked_dependencies, client_event, lambda_context
):
    mocked_dependencies.idempotency_service.reserve.side_effect = [
        Reservation(ReservationOutcome.ACQUIRED, IMAGE_ID),
        Reservation(ReservationOutcome.REPLAY, IMAGE_ID, 202, {"imageId": IMAGE_ID}),
    ]
    handler = load_handler(monkeypatch, mocked_dependencies)

    first = invoke(handler, client_event, lambda_context)
    second = invoke(handler, client_event, lambda_context)

    assert first["statusCode"] == second["statusCode"] == 202
    assert json.loads(first["body"]) == json.loads(second["body"]) == {"imageId": IMAGE_ID}
    assert first["headers"]["Retry-After"] == "5"
    mocked_dependencies.command_bus.handle.assert_called_once()
    command = mocked_dependencies.command_bus.handle.call_args.args[0]
    assert command.imageId == IMAGE_ID
    assert command.pipelineId.value == "pipe-1"
    assert re.fullmatch(r"[0-9a-f]{64}", command.clientToken)
    scope, request_hash, _, _ = mocked_dependencies.idempotency_service.reserve.call_args.args
    assert (scope.client_id, scope.project_id, scope.operation, scope.parent_resource_id, str(scope.key)) == (
        "client-1",
        "proj-1",
        "CREATE_IMAGE",
        None,
        KEY,
    )
    assert request_hash == mocked_dependencies.idempotency_service.complete.call_args.args[1]
    assert mocked_dependencies.idempotency_service.complete.call_args.args[2:5] == (
        IMAGE_ID,
        202,
        {"imageId": IMAGE_ID},
    )


@pytest.mark.parametrize("key", [None, "not-a-uuid"])
def test_create_image_requires_valid_key_before_reservation(
    monkeypatch, mocked_dependencies, client_event, lambda_context, key
):
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = invoke(handler, client_event, lambda_context, key=key)

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["code"] == "INVALID_IDEMPOTENCY_KEY"
    mocked_dependencies.idempotency_service.reserve.assert_not_called()
    mocked_dependencies.command_bus.handle.assert_not_called()


@pytest.mark.parametrize(
    ("outcome", "expected_code", "retry_after"),
    [
        (ReservationOutcome.CONFLICT, "IDEMPOTENCY_KEY_REUSED", None),
        (ReservationOutcome.IN_PROGRESS, "IDEMPOTENCY_REQUEST_IN_PROGRESS", "5"),
    ],
)
def test_create_image_rejects_different_body_or_live_lease(
    monkeypatch, mocked_dependencies, client_event, lambda_context, outcome, expected_code, retry_after
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(outcome, IMAGE_ID)
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = invoke(handler, client_event, lambda_context, pipeline_id="pipe-other")

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["code"] == expected_code
    assert response["headers"].get("Retry-After") == retry_after
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_create_image_scope_tracks_client_and_project_and_hashes_pipeline(
    monkeypatch, mocked_dependencies, client_event, lambda_context
):
    handler = load_handler(monkeypatch, mocked_dependencies)

    invoke(handler, client_event, lambda_context, client_id="client-1")
    invoke(handler, client_event, lambda_context, client_id="client-2", pipeline_id="pipe-other")
    invoke(handler, client_event, lambda_context, path="/projects/proj-2/images")

    calls = mocked_dependencies.idempotency_service.reserve.call_args_list
    assert [(call.args[0].client_id, call.args[0].project_id, call.args[0].operation) for call in calls] == [
        ("client-1", "proj-1", "CREATE_IMAGE"),
        ("client-2", "proj-1", "CREATE_IMAGE"),
        ("client-1", "proj-2", "CREATE_IMAGE"),
    ]
    assert calls[0].args[1] != calls[1].args[1]
    assert calls[0].args[1] == calls[2].args[1]
    assert CreateImageRequest.model_validate({"pipelineId": "pipe-1"}).pipelineId == "pipe-1"


@pytest.mark.parametrize("failure", ["access", "scope", "pipeline"])
def test_create_image_rejects_access_or_missing_pipeline_before_reservation(
    monkeypatch, mocked_dependencies, client_event, lambda_context, failure
):
    if failure == "access":
        mocked_dependencies.project_access_service.require_access.side_effect = ProjectAccessDenied()
    elif failure == "pipeline":
        mocked_dependencies.pipeline_domain_qry_srv.get_pipeline.return_value = None
    handler = load_handler(monkeypatch, mocked_dependencies)
    event = client_event(
        "POST",
        PATH,
        {"pipelineId": "pipe-1"},
        headers={"Idempotency-Key": KEY},
        scopes=[] if failure == "scope" else [SCOPE],
    )
    if failure == "scope":
        event["requestContext"]["authorizer"]["claims"]["scope"] = "clients/packaging/pipeline.read"

    response = handler.handler(event, lambda_context)

    assert response["statusCode"] == {"access": 403, "scope": 403, "pipeline": 404}[failure]
    mocked_dependencies.idempotency_service.reserve.assert_not_called()
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_create_image_recovers_existing_image_without_dispatch(
    monkeypatch, mocked_dependencies, client_event, lambda_context
):
    mocked_dependencies.idempotency_service.reserve.side_effect = None
    mocked_dependencies.idempotency_service.reserve.return_value = Reservation(ReservationOutcome.RECOVER, IMAGE_ID)
    mocked_dependencies.image_domain_qry_srv.get_image.return_value = object()
    handler = load_handler(monkeypatch, mocked_dependencies)

    response = invoke(handler, client_event, lambda_context)

    assert response["statusCode"] == 202
    assert json.loads(response["body"]) == {"imageId": IMAGE_ID}
    mocked_dependencies.command_bus.handle.assert_not_called()
    mocked_dependencies.idempotency_service.complete.assert_called_once()
    assert mocked_dependencies.image_domain_qry_srv.get_image.call_args.args[1].value == IMAGE_ID


def test_create_image_retries_ambiguous_upstream_success_with_same_identity(
    monkeypatch, mocked_dependencies, client_event, lambda_context
):
    mocked_dependencies.idempotency_service.reserve.side_effect = [
        Reservation(ReservationOutcome.ACQUIRED, IMAGE_ID),
        Reservation(ReservationOutcome.RECOVER, IMAGE_ID),
    ]
    upstream_executions = {}

    def start_then_persist(command):
        upstream_executions.setdefault(
            command.clientToken, "arn:aws:imagebuilder:eu-west-1:000000000:image/test/1.0.0/1"
        )
        if mocked_dependencies.command_bus.handle.call_count == 1:
            raise RuntimeError("persistence failed after upstream start")
        return command.imageId

    mocked_dependencies.command_bus.handle.side_effect = start_then_persist
    mocked_dependencies.image_domain_qry_srv.get_image.return_value = None
    handler = load_handler(monkeypatch, mocked_dependencies)

    first = invoke(handler, client_event, lambda_context)
    assert first["statusCode"] == 500
    mocked_dependencies.idempotency_service.complete.assert_not_called()
    second = invoke(handler, client_event, lambda_context)

    assert second["statusCode"] == 202
    assert json.loads(second["body"]) == {"imageId": IMAGE_ID}
    first_command, second_command = [call.args[0] for call in mocked_dependencies.command_bus.handle.call_args_list]
    assert first_command.imageId == second_command.imageId == IMAGE_ID
    assert first_command.clientToken == second_command.clientToken
    assert first_command.clientToken == derive_image_client_token(
        mocked_dependencies.idempotency_service.reserve.call_args.args[0], IMAGE_ID
    )
    assert len(upstream_executions) == 1
    mocked_dependencies.idempotency_service.complete.assert_called_once()
