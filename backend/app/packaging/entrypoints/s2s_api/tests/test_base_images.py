"""GET|PUT /base-images: the deployment's base image release channels per architecture."""

import importlib
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from app.packaging.domain.commands.image import release_base_image_command
from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.exceptions.s2s_exception import ProjectAccessDenied
from app.packaging.domain.model.recipe import base_image_channels

READ = "clients/packaging/base_image.read"
WRITE = "clients/packaging/base_image.write"
PLATFORM = "proj-platform"
PREFIX = "/vew/base-images"
CHANNELS = base_image_channels.BaseImageChannels(
    releasing_project_id=PLATFORM, parameter_prefix=PREFIX, os_version="Ubuntu 24.04 base"
)
VALUES = {
    f"{PREFIX}/test/arm64": "ami-0new",
    f"{PREFIX}/test/arm64/previous": "ami-0old",
    f"{PREFIX}/prod/arm64": "ami-0old",
}
IMAGES = {"ami-0new": "image-new", "ami-0old": "image-old"}


def _handler(monkeypatch, mocked_dependencies):
    mocked_dependencies.base_image_channels = CHANNELS
    mocked_dependencies.base_image_parameter_service = mock.Mock()
    mocked_dependencies.base_image_parameter_service.get_parameter_value.side_effect = VALUES.get
    mocked_dependencies.image_domain_qry_srv.get_image_by_image_upstream_id.side_effect = lambda ami: (
        SimpleNamespace(imageId=IMAGES[ami], projectId=PLATFORM) if ami in IMAGES else None
    )
    from app.packaging.entrypoints.s2s_api import bootstrapper

    monkeypatch.setattr(bootstrapper, "bootstrap", mock.Mock(return_value=mocked_dependencies))
    from app.packaging.entrypoints.s2s_api import handler

    return importlib.reload(handler)


def _body(response):
    return json.loads(response["body"])


def test_list_returns_both_channels_of_every_architecture(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = _handler(monkeypatch, mocked_dependencies)

    response = handler.handler(client_event("GET", "/base-images", scopes=[READ]), lambda_context)

    assert response["statusCode"] == 200
    images = {(i["channel"], i["architecture"]): i for i in _body(response)["baseImages"]}
    assert set(images) == {(c, a) for c in ("test", "prod") for a in ("amd64", "arm64")}
    assert images[("test", "arm64")] == {
        "architecture": "arm64",
        "channel": "test",
        "osVersion": "Ubuntu 24.04 base (test)",
        "parameterName": f"{PREFIX}/test/arm64",
        "status": "RELEASED",
        "projectId": PLATFORM,
        "imageId": "image-new",
        "amiId": "ami-0new",
        "previousAmiId": "ami-0old",
    }
    assert images[("prod", "arm64")]["osVersion"] == "Ubuntu 24.04 base"
    assert images[("prod", "amd64")]["status"] == "NOT_RELEASED"
    # Deployment-level: no project assignment is looked up for reads.
    mocked_dependencies.project_access_service.require_access.assert_not_called()


def test_get_one_channel_or_not_found(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies)

    found = handler.handler(client_event("GET", "/base-images/arm64/prod", scopes=[READ]), lambda_context)
    per_arch = handler.handler(client_event("GET", "/base-images/arm64", scopes=[READ]), lambda_context)
    missing = handler.handler(client_event("GET", "/base-images/x86/prod", scopes=[READ]), lambda_context)

    assert found["statusCode"] == 200
    assert _body(found)["imageId"] == "image-old"
    assert [i["channel"] for i in _body(per_arch)["baseImages"]] == ["prod", "test"]
    assert missing["statusCode"] == 404


def test_release_sends_the_command_and_returns_the_channel(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = _handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event(
            "PUT",
            "/base-images/arm64/test",
            body={"projectId": PLATFORM, "imageId": "image-new"},
            client_id="terraform",
            scopes=[WRITE],
        ),
        lambda_context,
    )

    assert response["statusCode"] == 200
    assert _body(response)["imageId"] == "image-new"
    (command,) = [c.args[0] for c in mocked_dependencies.command_bus.handle.call_args_list]
    assert isinstance(command, release_base_image_command.ReleaseBaseImageCommand)
    assert (command.projectId.value, command.imageId.value, command.architecture, command.channel) == (
        PLATFORM,
        "image-new",
        "arm64",
        "test",
    )
    assert command.releasedBy.value == "service:terraform"
    mocked_dependencies.project_access_service.require_access.assert_called_once_with("terraform", PLATFORM)


def test_only_the_releasing_project_releases(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies)

    other = handler.handler(
        client_event(
            "PUT", "/base-images/arm64/test", body={"projectId": "proj-other", "imageId": "i"}, scopes=[WRITE]
        ),
        lambda_context,
    )
    mocked_dependencies.project_access_service.require_access.side_effect = ProjectAccessDenied()
    unassigned = handler.handler(
        client_event("PUT", "/base-images/arm64/test", body={"projectId": PLATFORM, "imageId": "i"}, scopes=[WRITE]),
        lambda_context,
    )

    assert (other["statusCode"], _body(other)["code"]) == (403, "RELEASING_PROJECT_ONLY")
    assert (unassigned["statusCode"], _body(unassigned)["code"]) == (403, "PROJECT_ACCESS_DENIED")
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_prod_without_a_test_release_is_a_conflict(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies)
    mocked_dependencies.command_bus.handle.side_effect = domain_exception.BaseImageNotReleasedToRequiredChannel(
        "not in test"
    )

    response = handler.handler(
        client_event("PUT", "/base-images/arm64/prod", body={"projectId": PLATFORM, "imageId": "i"}, scopes=[WRITE]),
        lambda_context,
    )

    assert (response["statusCode"], _body(response)["code"]) == (409, "BASE_IMAGE_NOT_RELEASED_TO_REQUIRED_CHANNEL")


def test_an_invalid_image_is_a_domain_failure(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies)
    mocked_dependencies.command_bus.handle.side_effect = domain_exception.DomainException("a amd64 build")

    response = handler.handler(
        client_event("PUT", "/base-images/arm64/test", body={"projectId": PLATFORM, "imageId": "i"}, scopes=[WRITE]),
        lambda_context,
    )

    assert response["statusCode"] == 422


@pytest.mark.parametrize(
    "method,path,scope",
    [
        ("GET", "/base-images", WRITE),
        ("GET", "/base-images/arm64/test", WRITE),
        ("PUT", "/base-images/arm64/test", READ),
    ],
)
def test_base_images_need_their_scope(
    method, path, scope, monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = _handler(monkeypatch, mocked_dependencies)
    body = {"projectId": PLATFORM, "imageId": "i"} if method == "PUT" else None

    response = handler.handler(client_event(method, path, body=body, scopes=[scope]), lambda_context)

    assert response["statusCode"] == 403
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_without_a_releasing_project_there_are_no_channels(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = _handler(monkeypatch, mocked_dependencies)
    mocked_dependencies.base_image_channels = base_image_channels.BaseImageChannels()
    handler = importlib.reload(handler)

    listed = handler.handler(client_event("GET", "/base-images", scopes=[READ]), lambda_context)
    release = handler.handler(
        client_event("PUT", "/base-images/arm64/test", body={"projectId": PLATFORM, "imageId": "i"}, scopes=[WRITE]),
        lambda_context,
    )

    assert _body(listed)["baseImages"] == []
    assert release["statusCode"] == 404
    mocked_dependencies.command_bus.handle.assert_not_called()
