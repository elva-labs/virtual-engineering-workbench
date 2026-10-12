import json
from unittest import mock

from app.publishing.domain.commands import retire_version_command
from app.publishing.domain.exceptions import domain_exception
from app.publishing.domain.model import version
from app.publishing.domain.ports.service_client_project_access_service import ServiceClientProjectAccessService
from app.publishing.domain.ports.technologies_query_service import TechnologiesQueryService
from app.publishing.domain.ports.version_workbenches_query_service import VersionWorkbenchesQueryService
from app.publishing.entrypoints.s2s_api import bootstrapper
from app.publishing.entrypoints.s2s_api.tests.test_products import FakeIdempotencyService, FakeProducts, _body, _invoke
from app.publishing.entrypoints.s2s_api.tests.test_versions import FakeVersions, _distribution
from app.shared.adapters.message_bus.command_bus import CommandBus

READ = "clients/publishing/version.read"
RETIRE = "clients/publishing/version.retire"
PROMOTE = "clients/publishing/version.promote"
RETIREMENT = "/projects/proj-12345/products/prod-12345678/versions/vers-00000001/retirement"


def _dependencies(distributions, workbenches=()):
    products = FakeProducts()
    products.add()
    versions = FakeVersions(distributions)
    bus = mock.create_autospec(CommandBus, instance=True)

    def handle(command):
        if isinstance(command, retire_version_command.RetireVersionCommand):
            for d in versions.distributions:
                if d.versionId == command.versionId.value:
                    d.status = version.VersionStatus.Retiring
                    d.retireReason = command.retireReason

    bus.handle.side_effect = handle
    in_use = mock.create_autospec(VersionWorkbenchesQueryService, instance=True)
    in_use.get_active_workbench_ids.return_value = list(workbenches)
    return bootstrapper.Dependencies.model_construct(
        command_bus=bus,
        products_query_service=products,
        versions_query_service=versions,
        project_access_service=mock.create_autospec(ServiceClientProjectAccessService, instance=True),
        technologies_query_service=mock.create_autospec(TechnologiesQueryService, instance=True),
        idempotency_service=FakeIdempotencyService(),
        version_workbenches_query_service=in_use,
    )


def _retirements(dependencies):
    return [
        c.args[0]
        for c in dependencies.command_bus.handle.call_args_list
        if isinstance(c.args[0], retire_version_command.RetireVersionCommand)
    ]


def _put(dependencies, client_event, lambda_context, body=None, scopes=(RETIRE,)):
    event = client_event("PUT", RETIREMENT, json.dumps(body) if body is not None else None, scopes=list(scopes))
    return _invoke(dependencies, event, lambda_context)


def test_put_retires_once_as_program_owner_and_a_repeat_returns_the_state(client_event, lambda_context):
    dependencies = _dependencies([_distribution(), _distribution("PROD")])

    first = _put(dependencies, client_event, lambda_context, {"reason": "Superseded by 1.0.5"})
    repeated = _put(dependencies, client_event, lambda_context, {"reason": "Superseded by 1.0.5"})

    assert first["statusCode"] == 202
    assert first["multiValueHeaders"]["Retry-After"] == ["30"]
    assert _body(first)["status"] == "RETIRING"
    assert _body(first)["retireReason"] == "Superseded by 1.0.5"
    assert repeated["statusCode"] == 202
    (command,) = _retirements(dependencies)
    assert command.retiredBy.value == "service:client-1"
    assert [role.value for role in command.userRoles] == ["PROGRAM_OWNER"]
    dependencies.project_access_service.require_access.assert_called_with("client-1", "proj-12345")


def test_a_retired_version_answers_200_and_runs_nothing(client_event, lambda_context):
    dependencies = _dependencies(
        [_distribution(status=version.VersionStatus.Retired), _distribution("PROD", version.VersionStatus.Retired)]
    )

    response = _put(dependencies, client_event, lambda_context)

    assert response["statusCode"] == 200
    assert _body(response)["status"] == "RETIRED"
    assert _retirements(dependencies) == []
    dependencies.version_workbenches_query_service.get_active_workbench_ids.assert_not_called()


def test_workbenches_on_the_version_block_the_retirement_without_force(client_event, lambda_context):
    dependencies = _dependencies([_distribution()], workbenches=["vew-pp-1", "vew-pp-2"])

    refused = _put(dependencies, client_event, lambda_context)

    assert refused["statusCode"] == 409
    assert _body(refused)["code"] == "VERSION_IN_USE"
    assert "2 workbench(es)" in _body(refused)["detail"]
    assert _retirements(dependencies) == []
    dependencies.version_workbenches_query_service.get_active_workbench_ids.assert_called_with(
        "prod-12345678", "vers-00000001"
    )


def test_force_retires_despite_workbenches_on_it(client_event, lambda_context):
    dependencies = _dependencies([_distribution()], workbenches=["vew-pp-1"])

    response = _put(dependencies, client_event, lambda_context, {"force": True})

    assert response["statusCode"] == 202
    assert len(_retirements(dependencies)) == 1
    dependencies.version_workbenches_query_service.get_active_workbench_ids.assert_not_called()


def test_an_unknown_workbench_count_never_retires(client_event, lambda_context):
    dependencies = _dependencies([_distribution()])
    dependencies.version_workbenches_query_service = None

    response = _put(dependencies, client_event, lambda_context)

    assert response["statusCode"] == 503
    assert _retirements(dependencies) == []


def test_the_domain_refusal_of_the_latest_version_is_a_422(client_event, lambda_context):
    dependencies = _dependencies([_distribution()])
    dependencies.command_bus.handle.side_effect = domain_exception.DomainException("Latest version can not be retired")

    response = _put(dependencies, client_event, lambda_context)

    assert response["statusCode"] == 422
    assert _body(response)["code"] == "DOMAIN_VALIDATION_FAILED"


def test_get_imports_a_retirement_and_404s_before_it(client_event, lambda_context):
    retired = _dependencies([_distribution(status=version.VersionStatus.Retired)])
    live = _dependencies([_distribution()])

    found = _invoke(retired, client_event("GET", RETIREMENT, scopes=[READ]), lambda_context)
    missing = _invoke(live, client_event("GET", RETIREMENT, scopes=[READ]), lambda_context)

    assert found["statusCode"] == 200
    assert _body(found)["distributions"] == [
        {"awsAccountId": "123456789012", "region": "us-east-1", "status": "RETIRED"}
    ]
    assert missing["statusCode"] == 404


def test_retiring_needs_the_retire_scope_and_delete_only_forgets(client_event, lambda_context):
    dependencies = _dependencies([_distribution(status=version.VersionStatus.Retired)])

    promote_only = _put(dependencies, client_event, lambda_context, scopes=[PROMOTE])
    deleted = _invoke(dependencies, client_event("DELETE", RETIREMENT, scopes=[RETIRE]), lambda_context)

    assert promote_only["statusCode"] == 403
    assert deleted["statusCode"] == 204
    assert _retirements(dependencies) == []
    (distribution,) = dependencies.versions_query_service.get_product_version_distributions("prod-12345678")
    assert distribution.status == version.VersionStatus.Retired


def test_another_programs_version_is_not_found(client_event, lambda_context):
    dependencies = _dependencies([_distribution()])

    response = _invoke(
        dependencies,
        client_event(
            "PUT", "/projects/prog-other/products/prod-12345678/versions/vers-00000001/retirement", scopes=[RETIRE]
        ),
        lambda_context,
    )

    assert response["statusCode"] == 404
    assert _retirements(dependencies) == []


def test_an_unknown_body_field_is_rejected(client_event, lambda_context):
    dependencies = _dependencies([_distribution()])

    response = _put(dependencies, client_event, lambda_context, {"forced": True})

    assert response["statusCode"] == 400
    assert _retirements(dependencies) == []
