from types import SimpleNamespace

import pytest

from app.shared.middleware import authorization, read_only_clients

READ = "clients/projects/program.read"
WRITE = "clients/projects/program.write"


def service(client_id):
    return authorization.Principal(authType=authorization.AuthType.CognitoServiceJWT, userName=client_id)


def user(user_name):
    return authorization.Principal(authType=authorization.AuthType.CognitoUserJWT, userName=user_name)


class Refused(Exception):
    pass


def run_middleware(principal, method):
    app = SimpleNamespace(current_event=SimpleNamespace(http_method=method), context={"user_principal": principal})
    return read_only_clients.refuse_writes(Refused)(app, lambda _: "next")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv(read_only_clients.ENVIRONMENT_VARIABLE, " plan-client , other-reader,")


def test_none_configured_by_default(monkeypatch):
    monkeypatch.delenv(read_only_clients.ENVIRONMENT_VARIABLE, raising=False)
    assert read_only_clients.client_ids() == frozenset()
    assert not read_only_clients.is_read_only_platform_client(service("plan-client"))
    assert not read_only_clients.admits_without_assignment(service("plan-client"), {"httpMethod": "GET"}, READ)
    for method in ("GET", "PUT", "POST", "DELETE"):
        assert run_middleware(service("plan-client"), method) == "next"


def test_client_ids_are_a_comma_separated_list(configured):
    assert read_only_clients.client_ids() == frozenset({"plan-client", "other-reader"})


@pytest.mark.parametrize("method", ["GET", "get", "HEAD"])
def test_a_read_only_client_reads_without_an_assignment(configured, method):
    assert read_only_clients.admits_without_assignment(service("plan-client"), {"httpMethod": method}, READ)
    assert read_only_clients.admits_without_assignment(
        service("other-reader"), SimpleNamespace(http_method=method), "clients/packaging/component.read"
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "", None])
def test_a_read_only_client_is_never_admitted_for_another_method(configured, method):
    assert not read_only_clients.admits_without_assignment(service("plan-client"), {"httpMethod": method}, READ)


@pytest.mark.parametrize(
    "scope",
    [WRITE, "clients/projects/client_assignment.bootstrap", "clients/publishing/version.promote", "read", ""],
)
def test_a_read_only_client_is_never_admitted_for_a_scope_other_than_a_read(configured, scope):
    assert not read_only_clients.admits_without_assignment(service("plan-client"), {"httpMethod": "GET"}, scope)


def test_other_clients_and_users_are_never_admitted_without_an_assignment(configured):
    for principal in (service("terraform-client"), user("plan-client"), None, SimpleNamespace(user_name="plan-client")):
        assert not read_only_clients.is_read_only_platform_client(principal)
        assert not read_only_clients.admits_without_assignment(principal, {"httpMethod": "GET"}, READ)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_middleware_refuses_every_write_of_a_read_only_client(configured, method):
    with pytest.raises(Refused):
        run_middleware(service("plan-client"), method)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_the_middleware_passes_a_read_only_clients_reads(configured, method):
    assert run_middleware(service("plan-client"), method) == "next"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_the_middleware_passes_other_clients_and_users(configured, method):
    assert run_middleware(service("terraform-client"), method) == "next"
    assert run_middleware(user("plan-client"), method) == "next"
    assert run_middleware(None, method) == "next"
