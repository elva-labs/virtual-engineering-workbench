"""GET|PUT|DELETE /mandatory-components-lists (docs/api/s2s-contract.md)."""

import importlib
import json
from types import SimpleNamespace
from unittest import mock

from app.packaging.domain.command_handlers.component import delete_mandatory_components_list_command_handler
from app.packaging.domain.commands.component import (
    create_mandatory_components_list_command,
    delete_mandatory_components_list_command,
    update_mandatory_components_list_command,
)
from app.packaging.domain.exceptions.s2s_exception import ProjectAccessDenied
from app.packaging.domain.model.component import mandatory_components_list
from app.packaging.domain.model.recipe import base_image_channels
from app.packaging.domain.model.shared.component_version_entry import ComponentVersionEntry
from app.packaging.domain.value_objects.component import (
    component_platform_value_object,
    component_supported_architecture_value_object,
    component_supported_os_version_value_object,
)

READ = "clients/packaging/mandatory_components_list.read"
WRITE = "clients/packaging/mandatory_components_list.write"
PLATFORM = "proj-platform"
OS = "Ubuntu 24"
PATH = "/mandatory-components-lists/Linux/Ubuntu%2024/amd64"
VERSIONS = {
    ("comp-marker", "vers-m1"): SimpleNamespace(
        componentId="comp-marker",
        componentName="saab-compliance-marker",
        componentVersionId="vers-m1",
        componentVersionName="1.0.0",
    ),
    ("comp-audit", "vers-a1"): SimpleNamespace(
        componentId="comp-audit",
        componentName="saab-audit",
        componentVersionId="vers-a1",
        componentVersionName="2.0.0",
    ),
}


def _entity(os_version=OS, architecture="amd64"):
    return mandatory_components_list.MandatoryComponentsList(
        mandatoryComponentsListPlatform="Linux",
        mandatoryComponentsListOsVersion=os_version,
        mandatoryComponentsListArchitecture=architecture,
        mandatoryComponentsVersions=[
            ComponentVersionEntry(
                componentId="comp-audit",
                componentName="saab-audit",
                componentVersionId="vers-a1",
                componentVersionName="2.0.0",
                order=1,
                position="APPEND",
            ),
            ComponentVersionEntry(
                componentId="comp-marker",
                componentName="saab-compliance-marker",
                componentVersionId="vers-m1",
                componentVersionName="1.0.0",
                order=1,
                position="PREPEND",
            ),
        ],
        createDate="2026-10-01T00:00:00+00:00",
        createdBy="service:terraform",
        lastUpdateDate="2026-10-01T00:00:00+00:00",
        lastUpdatedBy="service:terraform",
    )


def _handler(monkeypatch, mocked_dependencies, stored=None):
    state = {"list": stored}
    mocked_dependencies.base_image_channels = base_image_channels.BaseImageChannels(
        releasing_project_id=PLATFORM, os_version="Golden Ubuntu"
    )
    mocked_dependencies.mandatory_components_list_qry_srv = mock.Mock()
    mocked_dependencies.mandatory_components_list_qry_srv.get_mandatory_components_list.side_effect = (
        lambda platform, os, architecture: (
            state["list"]
            if state["list"] is not None and (platform, os, architecture) == ("Linux", OS, "amd64")
            else None
        )
    )
    mocked_dependencies.mandatory_components_list_qry_srv.get_mandatory_components_lists.return_value = (
        [state["list"]] if state["list"] else []
    )
    mocked_dependencies.component_version_qry_srv.get_component_version.side_effect = (
        lambda component_id, version_id: VERSIONS.get((component_id, version_id))
    )

    def handle(command):
        if not isinstance(command, delete_mandatory_components_list_command.DeleteMandatoryComponentsListCommand):
            state["list"] = _entity()

    mocked_dependencies.command_bus.handle.side_effect = handle
    from app.packaging.entrypoints.s2s_api import bootstrapper

    monkeypatch.setattr(bootstrapper, "bootstrap", mock.Mock(return_value=mocked_dependencies))
    from app.packaging.entrypoints.s2s_api import handler

    return importlib.reload(handler)


def _body(response):
    return json.loads(response["body"])


def _put_body(project_id=PLATFORM):
    return {
        "projectId": project_id,
        "prependedComponentsVersions": [{"componentId": "comp-marker", "componentVersionId": "vers-m1"}],
        "appendedComponentsVersions": [{"componentId": "comp-audit", "componentVersionId": "vers-a1"}],
    }


def test_get_returns_the_list_split_by_position(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies, stored=_entity())

    response = handler.handler(client_event("GET", PATH, scopes=[READ]), lambda_context)

    assert response["statusCode"] == 200
    body = _body(response)
    assert (body["platform"], body["osVersion"], body["architecture"]) == ("Linux", OS, "amd64")
    assert [v["componentName"] for v in body["prependedComponentsVersions"]] == ["saab-compliance-marker"]
    assert [v["componentName"] for v in body["appendedComponentsVersions"]] == ["saab-audit"]
    # Platform-level, like the base images: no project assignment is looked up for reads.
    mocked_dependencies.project_access_service.require_access.assert_not_called()


def test_get_of_a_missing_or_unknown_list_is_not_found(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies)

    missing = handler.handler(client_event("GET", PATH, scopes=[READ]), lambda_context)
    unknown_os = handler.handler(
        client_event("GET", "/mandatory-components-lists/Linux/Ubuntu%2099/amd64", scopes=[READ]), lambda_context
    )

    assert missing["statusCode"] == 404
    assert unknown_os["statusCode"] == 404


def test_reads_need_the_read_scope(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies, stored=_entity())

    response = handler.handler(client_event("GET", "/mandatory-components-lists", scopes=[WRITE]), lambda_context)

    assert response["statusCode"] == 403


def test_list_returns_every_list(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies, stored=_entity())

    response = handler.handler(client_event("GET", "/mandatory-components-lists", scopes=[READ]), lambda_context)

    assert response["statusCode"] == 200
    assert [entry["osVersion"] for entry in _body(response)["mandatoryComponentsLists"]] == [OS]


def test_put_creates_a_missing_list_with_the_resolved_names(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = _handler(monkeypatch, mocked_dependencies)

    response = handler.handler(
        client_event("PUT", PATH, body=_put_body(), client_id="terraform", scopes=[WRITE]), lambda_context
    )

    assert response["statusCode"] == 200
    (command,) = [c.args[0] for c in mocked_dependencies.command_bus.handle.call_args_list]
    assert isinstance(command, create_mandatory_components_list_command.CreateMandatoryComponentsListCommand)
    assert command.mandatoryComponentsListOsVersion.value == OS
    assert [e.componentName for e in command.prependedComponentsVersions.value] == ["saab-compliance-marker"]
    assert [e.componentVersionName for e in command.appendedComponentsVersions.value] == ["2.0.0"]
    assert command.createdBy.value == "service:terraform"
    mocked_dependencies.project_access_service.require_access.assert_called_once_with("terraform", PLATFORM)


def test_put_updates_an_existing_list(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies, stored=_entity())

    response = handler.handler(client_event("PUT", PATH, body=_put_body(), scopes=[WRITE]), lambda_context)

    assert response["statusCode"] == 200
    (command,) = [c.args[0] for c in mocked_dependencies.command_bus.handle.call_args_list]
    assert isinstance(command, update_mandatory_components_list_command.UpdateMandatoryComponentsListCommand)


def test_put_of_an_unknown_component_version_is_a_domain_error(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = _handler(monkeypatch, mocked_dependencies)
    body = _put_body()
    body["appendedComponentsVersions"] = [{"componentId": "comp-x", "componentVersionId": "vers-x"}]

    response = handler.handler(client_event("PUT", PATH, body=body, scopes=[WRITE]), lambda_context)

    assert response["statusCode"] == 422
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_only_the_releasing_project_changes_the_lists(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies)

    other = handler.handler(client_event("PUT", PATH, body=_put_body("proj-other"), scopes=[WRITE]), lambda_context)
    mocked_dependencies.project_access_service.require_access.side_effect = ProjectAccessDenied()
    unassigned = handler.handler(client_event("PUT", PATH, body=_put_body(), scopes=[WRITE]), lambda_context)
    delete_other = handler.handler(
        client_event("DELETE", PATH, query={"projectId": "proj-other"}, scopes=[WRITE]), lambda_context
    )

    assert _body(other)["code"] == "RELEASING_PROJECT_ONLY"
    assert unassigned["statusCode"] == 403
    assert _body(delete_other)["code"] == "RELEASING_PROJECT_ONLY"
    mocked_dependencies.command_bus.handle.assert_not_called()


def test_delete_is_idempotent(monkeypatch, mocked_dependencies, lambda_context, client_event):
    handler = _handler(monkeypatch, mocked_dependencies)

    first = handler.handler(client_event("DELETE", PATH, query={"projectId": PLATFORM}, scopes=[WRITE]), lambda_context)
    again = handler.handler(client_event("DELETE", PATH, query={"projectId": PLATFORM}, scopes=[WRITE]), lambda_context)

    assert first["statusCode"] == again["statusCode"] == 204
    commands = [c.args[0] for c in mocked_dependencies.command_bus.handle.call_args_list]
    assert all(
        isinstance(c, delete_mandatory_components_list_command.DeleteMandatoryComponentsListCommand) for c in commands
    )


def _delete_command():
    return delete_mandatory_components_list_command.DeleteMandatoryComponentsListCommand(
        mandatoryComponentsListPlatform=component_platform_value_object.from_str("Linux"),
        mandatoryComponentsListOsVersion=component_supported_os_version_value_object.from_str(OS),
        mandatoryComponentsListArchitecture=component_supported_architecture_value_object.from_str("amd64"),
    )


def test_delete_handler_removes_an_existing_list():
    query = mock.Mock()
    query.get_mandatory_components_list.return_value = _entity()
    uow = mock.MagicMock()

    assert delete_mandatory_components_list_command_handler.handle(_delete_command(), query, uow) is True
    uow.get_repository.return_value.remove.assert_called_once()
    uow.commit.assert_called_once()


def test_delete_handler_of_a_missing_list_does_nothing():
    query = mock.Mock()
    query.get_mandatory_components_list.return_value = None
    uow = mock.MagicMock()

    assert delete_mandatory_components_list_command_handler.handle(_delete_command(), query, uow) is False
    uow.get_repository.assert_not_called()


def test_without_a_releasing_project_any_assigned_project_changes_the_lists(
    monkeypatch, mocked_dependencies, lambda_context, client_event
):
    handler = _handler(monkeypatch, mocked_dependencies)
    mocked_dependencies.base_image_channels = base_image_channels.BaseImageChannels()

    response = handler.handler(
        client_event("PUT", PATH, body=_put_body("proj-other"), client_id="sample", scopes=[WRITE]), lambda_context
    )

    assert response["statusCode"] == 200
    mocked_dependencies.project_access_service.require_access.assert_called_once_with("sample", "proj-other")
