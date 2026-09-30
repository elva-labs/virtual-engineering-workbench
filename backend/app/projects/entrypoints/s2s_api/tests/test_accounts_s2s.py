import importlib
import json
from http import HTTPStatus
from unittest import mock
from unittest.mock import Mock

from aws_lambda_powertools.event_handler import api_gateway
from aws_lambda_powertools.event_handler.middlewares.openapi_validation import RequestValidationError

from app.projects.domain.commands.project_accounts import (
    deactivate_project_account_s2s_command,
    on_board_project_account_command,
    update_project_account_command,
)
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_account, technology
from app.projects.entrypoints.s2s_api import bootstrapper, common, problem_details, s2s_exception
from app.projects.entrypoints.s2s_api.routers import accounts
from app.projects.entrypoints.s2s_api.tests import fake_classes
from app.shared.adapters.message_bus import in_memory_command_bus
from app.shared.domain.ports.idempotency_service import Reservation, ReservationOutcome


def _account(**overrides):
    values = {
        "id": "account-record-id",
        "projectId": "project-1",
        "awsAccountId": "123456789012",
        "accountType": "USER",
        "accountName": "Development",
        "accountDescription": "Development account",
        "technologyId": "technology-1",
        "stage": "dev",
        "region": "us-east-1",
        "accountStatus": "Inactive",
        "parameters": {"secret": "never-return"},
    }
    values.update(overrides)
    return project_account.ProjectAccount(**values)


def _request_body(**overrides):
    values = {
        "awsAccountId": "123456789012",
        "accountType": "USER",
        "name": "Development",
        "description": "Development account",
        "technologyId": "technology-1",
        "stage": "dev",
        "region": "us-east-1",
    }
    values.update(overrides)
    return json.dumps(values)


def _update_request_body(**overrides):
    values = {
        "accountType": "USER",
        "name": "Development",
        "description": "Development account",
        "technologyId": "technology-1",
        "stage": "dev",
        "region": "us-east-1",
    }
    values.update(overrides)
    return json.dumps(values)


def _dependencies(accounts_list=None, global_accounts=None):
    project_queries = fake_classes.FakeProjectsQueryService()
    project_queries.get_service_client_assignment = Mock(return_value=Mock(status="ACTIVE"))
    project_queries.list_project_accounts = Mock(return_value=accounts_list or [])
    project_queries.list_project_accounts_by_aws_account = Mock(return_value=global_accounts or [])
    project_queries.get_project_account_by_id = Mock(return_value=None)

    technology_queries = fake_classes.FakeTechnologiesQueryService()
    technology_queries.get_technology_by_id = Mock(
        return_value=technology.Technology(
            id="technology-1",
            project_id="project-1",
            name="Technology",
            description=None,
        )
    )

    handled_commands = []
    update_commands = []
    delete_commands = []
    command_bus = in_memory_command_bus.InMemoryCommandBus(logger=Mock()).register_handler(
        on_board_project_account_command.OnBoardProjectAccountCommand,
        handled_commands.append,
    )
    command_bus.register_handler(
        update_project_account_command.UpdateProjectAccountCommand,
        update_commands.append,
    )
    command_bus.register_handler(
        deactivate_project_account_s2s_command.DeactivateProjectAccountS2SCommand,
        delete_commands.append,
    )
    project_queries.update_commands = update_commands
    project_queries.delete_commands = delete_commands
    dependencies = bootstrapper.Dependencies(
        command_bus=command_bus,
        projects_query_service=project_queries,
        technologies_query_service=technology_queries,
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(),
        idempotency_service=fake_classes.FakeIdempotencyService(),
    )
    return dependencies, project_queries, handled_commands


def _resolver(dependencies):
    app = api_gateway.APIGatewayRestResolver(enable_validation=True)
    app.include_router(accounts.init(dependencies))

    @app.exception_handler(s2s_exception.S2SException)
    def _handle_s2s_exception(error):
        return problem_details.api_response(error, "request-id")

    @app.exception_handler(RequestValidationError)
    def _handle_validation_exception(error):
        return problem_details.api_response(s2s_exception.InvalidRequest(), "request-id")

    @app.exception_handler(domain_exception.DomainException)
    def _handle_domain_exception(error):
        problem = problem_details.response(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="The request failed Projects validation.",
            code="DOMAIN_VALIDATION_FAILED",
            request_id="request-id",
            retryable=False,
        )
        return api_gateway.Response(
            status_code=problem["statusCode"],
            body=problem["body"],
            headers=problem["headers"],
            content_type=problem_details.PROBLEM_CONTENT_TYPE,
        )

    return app


def _set_update_handler(dependencies, handler):
    dependencies.command_bus._command_handlers[update_project_account_command.UpdateProjectAccountCommand.__name__] = (
        handler
    )


def _set_delete_handler(dependencies, handler):
    dependencies.command_bus._command_handlers[
        deactivate_project_account_s2s_command.DeactivateProjectAccountS2SCommand.__name__
    ] = handler


def _event(path, method, body=None, scope=None, idempotency_key=None):
    headers = {"Accept": "application/json"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return {
        "resource": path,
        "path": path,
        "httpMethod": method,
        "headers": headers,
        "requestContext": {
            "authorizer": {"claims": {"scope": scope or ""}},
            "requestId": "request-id",
        },
        "body": body,
        "isBase64Encoded": False,
    }


def _invoke_authenticated(dependencies, authenticated_event, body, path, method, scope, context, *, key=None):
    event = authenticated_event(body, path, method)
    event["requestContext"]["authorizer"]["claims"]["scope"] = scope
    if key is not None:
        event["headers"]["Idempotency-Key"] = key
    with mock.patch("app.projects.entrypoints.s2s_api.bootstrapper.bootstrap", return_value=dependencies):
        from app.projects.entrypoints.s2s_api import handler

        importlib.reload(handler)
        return handler.handler(event, context)


def test_account_list_and_exact_read_use_safe_canonical_projection(monkeypatch, lambda_context):
    private_error = "arn:aws:states:us-east-1:123456789012:execution:internal-secret"
    account = _account(
        accountStatus="Failed",
        lastOnboardingResult="Failed",
        lastOnboardingErrorMessage=private_error,
    )
    dependencies, project_queries, _ = _dependencies(accounts_list=[account])
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    app = _resolver(dependencies)

    listed = app.resolve(
        _event(
            "/projects/project-1/accounts",
            "GET",
            scope="clients/projects/account.read",
        ),
        lambda_context,
    )
    exact = app.resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "GET",
            scope="clients/projects/account.read",
        ),
        lambda_context,
    )

    listed_account = json.loads(listed["body"])["accounts"][0]
    exact_account = json.loads(exact["body"])
    assert listed["statusCode"] == HTTPStatus.OK
    assert exact["statusCode"] == HTTPStatus.OK
    assert set(listed_account) == {
        "accountId",
        "projectId",
        "awsAccountId",
        "accountType",
        "name",
        "description",
        "technologyId",
        "stage",
        "region",
        "status",
        "lastOnboardingResult",
        "lastOnboardingError",
        "onboardingRevision",
        "onboardedAt",
        "createDate",
        "lastUpdateDate",
    }
    assert "parameters" not in listed_account
    assert private_error not in listed["body"]
    assert exact_account["status"] == "Failed"
    assert exact_account["lastOnboardingError"] == (
        "Account onboarding failed. Consult VEW onboarding logs for details."
    )


def test_account_exact_read_exposes_active_and_pending_poll_statuses(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    app = _resolver(dependencies)

    for status in ("Active", "OnBoarding", "ReOnboarding"):
        account = _account(accountStatus=status)
        project_queries.get_project_account_by_id.return_value = account
        result = app.resolve(
            _event(
                "/projects/project-1/accounts/account-record-id",
                "GET",
                scope="clients/projects/account.read",
            ),
            lambda_context,
        )
        assert result["statusCode"] == HTTPStatus.OK
        assert json.loads(result["body"])["status"] == status


def test_create_reuses_inactive_account_id_and_replays_response(monkeypatch, lambda_context):
    inactive = _account()
    dependencies, project_queries, handled_commands = _dependencies(global_accounts=[inactive])
    project_queries.get_project_account_by_id.side_effect = lambda project_id, account_id: (
        inactive if account_id == inactive.id else None
    )
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    app = _resolver(dependencies)
    idempotency_key = "e09bcf2e-650c-497d-9974-e80c251f0ad6"

    event = _event(
        "/projects/project-1/accounts",
        "POST",
        body=_request_body(),
        scope="clients/projects/account.write",
        idempotency_key=idempotency_key,
    )
    first = app.resolve(event, lambda_context)
    replay = app.resolve(event, lambda_context)

    assert first["statusCode"] == HTTPStatus.ACCEPTED
    assert first["multiValueHeaders"]["Retry-After"] == ["5"]
    assert json.loads(first["body"]) == {"accountId": inactive.id}
    assert replay["statusCode"] == HTTPStatus.ACCEPTED
    assert json.loads(replay["body"]) == {"accountId": inactive.id}
    assert len(handled_commands) == 1
    assert handled_commands[0].reserved_account_id == inactive.id
    reservation = next(iter(dependencies.idempotency_service.records.values()))
    assert reservation["resource_id"] == inactive.id


def test_create_recovers_with_the_reserved_inactive_account_id(monkeypatch, lambda_context):
    inactive = _account()
    dependencies, project_queries, handled_commands = _dependencies(global_accounts=[inactive])
    project_queries.get_project_account_by_id.side_effect = lambda project_id, account_id: (
        inactive if account_id == inactive.id else None
    )

    class RecoveringIdempotencyService(fake_classes.FakeIdempotencyService):
        def reserve(self, scope, request_hash, resource_id, now):
            self.records[scope] = {
                "hash": request_hash,
                "resource_id": resource_id,
                "status": "IN_PROGRESS",
            }
            return Reservation(ReservationOutcome.RECOVER, resource_id)

    dependencies.idempotency_service = RecoveringIdempotencyService()
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    app = _resolver(dependencies)

    result = app.resolve(
        _event(
            "/projects/project-1/accounts",
            "POST",
            body=_request_body(),
            scope="clients/projects/account.write",
            idempotency_key="c55bcf2e-650c-497d-9974-e80c251f0ad6",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.ACCEPTED
    assert json.loads(result["body"]) == {"accountId": inactive.id}
    assert len(handled_commands) == 1
    assert handled_commands[0].reserved_account_id == inactive.id
    reservation = next(iter(dependencies.idempotency_service.records.values()))
    assert reservation["resource_id"] == inactive.id


def test_create_rejects_malformed_region_as_invalid_request(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    app = _resolver(dependencies)

    for region, key in (
        ("us--1", "a33bcf2e-650c-497d-9974-e80c251f0ad6"),
        ("us-foo-1", "d66bcf2e-650c-497d-9974-e80c251f0ad6"),
    ):
        result = app.resolve(
            _event(
                "/projects/project-1/accounts",
                "POST",
                body=_request_body(region=region),
                scope="clients/projects/account.write",
                idempotency_key=key,
            ),
            lambda_context,
        )
        assert result["statusCode"] == HTTPStatus.BAD_REQUEST
    project_queries.list_project_accounts_by_aws_account.assert_not_called()


def test_create_conflicts_when_aws_account_is_owned_by_another_project(monkeypatch, lambda_context):
    other_project_account = _account(projectId="project-other", id="other-id")
    dependencies, _, handled_commands = _dependencies(global_accounts=[other_project_account])
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    app = _resolver(dependencies)

    event = _event(
        "/projects/project-1/accounts",
        "POST",
        body=_request_body(),
        scope="clients/projects/account.write",
        idempotency_key="b44bcf2e-650c-497d-9974-e80c251f0ad6",
    )
    result = app.resolve(event, lambda_context)
    replay = app.resolve(event, lambda_context)

    assert result["statusCode"] == HTTPStatus.CONFLICT
    assert json.loads(result["body"])["code"] == "RESOURCE_CONFLICT"
    assert replay["statusCode"] == HTTPStatus.CONFLICT
    assert json.loads(replay["body"])["code"] == "RESOURCE_CONFLICT"
    assert handled_commands == []


def test_account_create_changed_request_with_same_key_conflicts(monkeypatch, lambda_context):
    dependencies, _, handled_commands = _dependencies()
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    app = _resolver(dependencies)
    key = "f0cbc6bd-28a8-4adc-8bb6-29d4293fd571"

    first = app.resolve(
        _event(
            "/projects/project-1/accounts",
            "POST",
            body=_request_body(),
            scope="clients/projects/account.write",
            idempotency_key=key,
        ),
        lambda_context,
    )
    changed = app.resolve(
        _event(
            "/projects/project-1/accounts",
            "POST",
            body=_request_body(name="Different"),
            scope="clients/projects/account.write",
            idempotency_key=key,
        ),
        lambda_context,
    )

    assert first["statusCode"] == HTTPStatus.ACCEPTED
    assert changed["statusCode"] == HTTPStatus.CONFLICT
    assert json.loads(changed["body"])["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(handled_commands) == 1


def test_account_routes_check_scope_and_assignment_before_resource_access(authenticated_event, lambda_context):
    dependencies, project_queries, handled_commands = _dependencies()
    project_queries.list_project_accounts = Mock()
    missing_scope = _invoke_authenticated(
        dependencies,
        authenticated_event,
        None,
        "/projects/project-1/accounts",
        "GET",
        "clients/projects/account.write",
        lambda_context,
    )
    assert missing_scope["statusCode"] == HTTPStatus.FORBIDDEN
    assert json.loads(missing_scope["body"])["code"] == "INSUFFICIENT_SCOPE"
    project_queries.list_project_accounts.assert_not_called()

    project_queries.get_service_client_assignment.return_value = None
    denied = _invoke_authenticated(
        dependencies,
        authenticated_event,
        _request_body(),
        "/projects/project-1/accounts",
        "POST",
        "clients/projects/account.write",
        lambda_context,
        key="18570ee3-e895-4684-81d3-495f3339a011",
    )
    assert denied["statusCode"] == HTTPStatus.FORBIDDEN
    assert json.loads(denied["body"])["code"] == "PROJECT_ACCESS_DENIED"
    assert handled_commands == []
    assert dependencies.idempotency_service.records == {}


def test_update_metadata_only_returns_canonical_account(monkeypatch, lambda_context):
    account = _account(
        accountStatus="Active",
        lastOnboardingResult="Succeeded",
    )
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    def update(command):
        project_queries.update_commands.append(command)
        account.accountName = command.account_name.value
        account.accountDescription = command.account_description.value

    _set_update_handler(dependencies, update)
    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(name="Renamed", description="New description"),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    body = json.loads(result["body"])
    assert result["statusCode"] == HTTPStatus.OK
    assert body["accountId"] == account.id
    assert body["name"] == "Renamed"
    assert body["description"] == "New description"
    assert "parameters" not in body
    assert project_queries.update_commands[0].account_id.value == account.id


def test_update_identical_successful_configuration_is_noop(monkeypatch, lambda_context):
    account = _account(accountStatus="Active", lastOnboardingResult="Succeeded")
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    _set_update_handler(dependencies, project_queries.update_commands.append)

    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.OK
    assert json.loads(result["body"])["status"] == "Active"
    assert len(project_queries.update_commands) == 1


def test_update_failed_account_stays_readable_and_rejects_mutation(monkeypatch, lambda_context):
    account = _account(
        accountStatus="Failed",
        lastOnboardingResult="Failed",
        lastOnboardingErrorMessage="private workflow exception text",
    )
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    def reject_failed_update(command):
        raise domain_exception.DomainException("private workflow exception text")

    _set_update_handler(dependencies, reject_failed_update)
    app = _resolver(dependencies)
    update_result = app.resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(region="us-west-2"),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )
    read_result = app.resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "GET",
            scope="clients/projects/account.read",
        ),
        lambda_context,
    )

    assert update_result["statusCode"] == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "private workflow exception text" not in update_result["body"]
    assert read_result["statusCode"] == HTTPStatus.OK
    read_body = json.loads(read_result["body"])
    assert read_body["status"] == "Failed"
    assert "private workflow exception text" not in read_result["body"]


def test_update_operational_change_returns_accepted(monkeypatch, lambda_context):
    account = _account(accountStatus="Active", lastOnboardingResult="Succeeded")
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    def update(command):
        project_queries.update_commands.append(command)
        account.accountType = command.account_type.value
        account.accountName = command.account_name.value
        account.accountDescription = command.account_description.value
        account.technologyId = command.technology.value
        account.stage = command.stage
        account.region = command.region.value
        account.accountStatus = project_account.ProjectAccountStatusEnum.ReOnboarding
        account.onboardingOperationId = "operation-1"
        account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Published

    _set_update_handler(dependencies, update)
    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(region="us-west-2"),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.ACCEPTED
    assert result["multiValueHeaders"]["Retry-After"] == ["5"]
    assert json.loads(result["body"]) == {"accountId": account.id}


def test_update_rejects_technology_outside_project(monkeypatch, lambda_context):
    account = _account(accountStatus="Active")
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    dependencies.technologies_query_service.get_technology_by_id.return_value = None
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.BAD_REQUEST
    assert project_queries.update_commands == []


def test_update_rejects_immutable_account_identity_in_body(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    request = json.loads(_update_request_body())
    request["awsAccountId"] = "999999999999"

    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=json.dumps(request),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.BAD_REQUEST
    project_queries.get_project_account_by_id.assert_not_called()


def test_update_missing_account_returns_not_found_before_technology_lookup(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/missing-account",
            "PUT",
            body=_update_request_body(),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.NOT_FOUND
    dependencies.technologies_query_service.get_technology_by_id.assert_not_called()
    assert project_queries.update_commands == []


def test_update_requires_scope_and_active_assignment(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = _account(accountStatus="Active")
    app = _resolver(dependencies)

    for error, expected in (
        (s2s_exception.InsufficientScope(), HTTPStatus.FORBIDDEN),
        (s2s_exception.ProjectAccessDenied(), HTTPStatus.FORBIDDEN),
    ):
        monkeypatch.setattr(common, "authorize", Mock(side_effect=error))
        result = app.resolve(
            _event(
                "/projects/project-1/accounts/account-record-id",
                "PUT",
                body=_update_request_body(),
                scope="clients/projects/account.write",
            ),
            lambda_context,
        )
        assert result["statusCode"] == expected
    project_queries.get_project_account_by_id.assert_not_called()


def test_update_retry_resumes_pending_operation(monkeypatch, lambda_context):
    account = _account(accountStatus="Active", lastOnboardingResult="Succeeded")
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    attempts = []

    def update(command):
        attempts.append(command)
        if len(attempts) == 1:
            account.region = command.region.value
            account.accountStatus = project_account.ProjectAccountStatusEnum.ReOnboarding
            account.onboardingOperationId = "operation-pending"
            account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Pending
            raise RuntimeError("simulated publication interruption")
        assert account.onboardingOperationId == "operation-pending"
        account.onboardingPublicationStatus = project_account.ProjectAccountOnboardingPublicationStatus.Published

    _set_update_handler(dependencies, update)
    app = _resolver(dependencies)
    event = _event(
        "/projects/project-1/accounts/account-record-id",
        "PUT",
        body=_update_request_body(region="us-west-2"),
        scope="clients/projects/account.write",
    )
    interrupted = app.resolve(event, lambda_context)
    resumed = app.resolve(event, lambda_context)

    assert interrupted["statusCode"] == HTTPStatus.SERVICE_UNAVAILABLE
    assert resumed["statusCode"] == HTTPStatus.ACCEPTED
    assert len(attempts) == 2
    assert account.onboardingOperationId == "operation-pending"


def test_delete_deactivates_and_repeats_as_noop(monkeypatch, lambda_context):
    account = _account(accountStatus="Active")
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    def deactivate(command):
        project_queries.delete_commands.append(command)
        if account.accountStatus == project_account.ProjectAccountStatusEnum.Inactive:
            return
        if account.accountStatus != project_account.ProjectAccountStatusEnum.Active:
            raise domain_exception.ProjectAccountStateConflict()
        account.accountStatus = project_account.ProjectAccountStatusEnum.Inactive

    _set_delete_handler(dependencies, deactivate)
    app = _resolver(dependencies)
    event = _event(
        "/projects/project-1/accounts/account-record-id",
        "DELETE",
        scope="clients/projects/account.write",
    )

    first = app.resolve(event, lambda_context)
    repeated = app.resolve(event, lambda_context)

    assert first["statusCode"] == HTTPStatus.NO_CONTENT
    assert repeated["statusCode"] == HTTPStatus.NO_CONTENT
    assert account.accountStatus == project_account.ProjectAccountStatusEnum.Inactive
    assert len(project_queries.delete_commands) == 2


def test_delete_of_other_projects_account_is_isolated_noop(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    other_project_account = _account(projectId="project-other", accountStatus="Active")
    project_queries.get_project_account_by_id.return_value = None
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    def guarded_delete(command):
        project_queries.delete_commands.append(command)
        assert command.project_id.value == "project-1"
        assert other_project_account.projectId == "project-other"

    _set_delete_handler(dependencies, guarded_delete)
    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "DELETE",
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.NO_CONTENT
    assert other_project_account.accountStatus == project_account.ProjectAccountStatusEnum.Active


def test_delete_inflight_account_returns_conflict(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = _account(accountStatus="ReOnboarding")
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    def reject_inflight(command):
        raise domain_exception.ProjectAccountStateConflict("private domain message")

    _set_delete_handler(dependencies, reject_inflight)
    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "DELETE",
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.CONFLICT
    assert json.loads(result["body"])["code"] == "RESOURCE_CONFLICT"
    assert "private domain message" not in result["body"]


def test_handler_maps_domain_exception_to_sanitized_422(monkeypatch):
    dependencies, _, _ = _dependencies()
    with mock.patch(
        "app.projects.entrypoints.s2s_api.bootstrapper.bootstrap",
        return_value=dependencies,
    ):
        handler = importlib.import_module("app.projects.entrypoints.s2s_api.handler")
        handler = importlib.reload(handler)
    monkeypatch.setattr(handler, "_request_id", Mock(return_value="request-id"))

    response = handler.handle_domain_error(domain_exception.DomainException("private workflow message"))

    assert isinstance(response, api_gateway.Response)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "private workflow message" not in response.body
    assert response.headers["Content-Type"] == problem_details.PROBLEM_CONTENT_TYPE
    assert response.headers["Cache-Control"] == "no-store"


def test_update_passes_onboarding_revision_and_returns_it(monkeypatch, lambda_context):
    # ADR 0022: vew_project_account.onboarding_revision re-runs onboarding from Terraform.
    account = _account(accountStatus="Active", lastOnboardingResult="Succeeded")
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    def update(command):
        project_queries.update_commands.append(command)
        account.onboardingRevision = command.onboarding_revision

    _set_update_handler(dependencies, update)
    app = _resolver(dependencies)
    result = app.resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(onboardingRevision="spoke-stacks-2026-10-01"),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] == HTTPStatus.OK
    assert project_queries.update_commands[0].onboarding_revision == "spoke-stacks-2026-10-01"
    assert json.loads(result["body"])["onboardingRevision"] == "spoke-stacks-2026-10-01"


def test_update_without_onboarding_revision_sends_none(monkeypatch, lambda_context):
    account = _account(accountStatus="Active", lastOnboardingResult="Succeeded")
    dependencies, project_queries, _ = _dependencies()
    project_queries.get_project_account_by_id.return_value = account
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))
    _set_update_handler(dependencies, project_queries.update_commands.append)

    _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert project_queries.update_commands[0].onboarding_revision is None


def test_update_rejects_malformed_onboarding_revision(monkeypatch, lambda_context):
    dependencies, project_queries, _ = _dependencies()
    monkeypatch.setattr(common, "authorize", Mock(return_value="client-1"))

    result = _resolver(dependencies).resolve(
        _event(
            "/projects/project-1/accounts/account-record-id",
            "PUT",
            body=_update_request_body(onboardingRevision="has spaces/and slashes"),
            scope="clients/projects/account.write",
        ),
        lambda_context,
    )

    assert result["statusCode"] in (HTTPStatus.BAD_REQUEST, HTTPStatus.UNPROCESSABLE_ENTITY)
    assert project_queries.update_commands == []
