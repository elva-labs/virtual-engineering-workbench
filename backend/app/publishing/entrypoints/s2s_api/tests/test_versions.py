from unittest import mock

from app.publishing.domain.commands import promote_version_command
from app.publishing.domain.exceptions import domain_exception
from app.publishing.domain.model import version
from app.publishing.domain.ports.service_client_project_access_service import ServiceClientProjectAccessService
from app.publishing.entrypoints.s2s_api import bootstrapper
from app.publishing.entrypoints.s2s_api.tests.test_products import FakeIdempotencyService, FakeProducts, _body, _invoke
from app.shared.adapters.message_bus.command_bus import CommandBus

READ = "clients/publishing/version.read"
PROMOTE = "clients/publishing/version.promote"
PROMOTION = "/projects/proj-12345/products/prod-12345678/versions/vers-00000001/stages"


def _distribution(stage="DEV", status=version.VersionStatus.Created, project_id="proj-12345", account="123456789012"):
    return version.Version(
        projectId=project_id,
        productId="prod-12345678",
        technologyId="tech-1",
        versionId="vers-00000001",
        versionName="1.0.0-rc.1",
        versionType=version.VersionType.ReleaseCandidate.text,
        awsAccountId=account,
        stage=stage,
        region="us-east-1",
        status=status,
        scPortfolioId="port-1",
        isRecommendedVersion=False,
        createDate="2026-09-29T00:00:00+00:00",
        lastUpdateDate="2026-09-29T00:00:00+00:00",
        createdBy="service:client-1",
        lastUpdatedBy="service:client-1",
    )


class FakeVersions:
    def __init__(self, distributions):
        self.distributions = list(distributions)

    def get_product_version_distributions(self, product_id, version_id=None, **_):
        return [
            d
            for d in self.distributions
            if d.productId == product_id and (version_id is None or d.versionId == version_id)
        ]


def _dependencies(distributions):
    products = FakeProducts()
    products.add()
    versions = FakeVersions(distributions)
    bus = mock.create_autospec(CommandBus, instance=True)

    def handle(command):
        if isinstance(command, promote_version_command.PromoteVersionCommand):
            versions.distributions.append(_distribution(command.stage.value, version.VersionStatus.Creating))

    bus.handle.side_effect = handle
    return bootstrapper.Dependencies.model_construct(
        command_bus=bus,
        products_query_service=products,
        versions_query_service=versions,
        project_access_service=mock.create_autospec(ServiceClientProjectAccessService, instance=True),
        idempotency_service=FakeIdempotencyService(),
    )


def _promotions(dependencies):
    return [
        c.args[0]
        for c in dependencies.command_bus.handle.call_args_list
        if isinstance(c.args[0], promote_version_command.PromoteVersionCommand)
    ]


def test_put_promotes_the_dev_version_to_prod_and_a_repeat_returns_the_state(client_event, lambda_context):
    dependencies = _dependencies([_distribution()])

    first = _invoke(dependencies, client_event("PUT", f"{PROMOTION}/PROD", scopes=[PROMOTE]), lambda_context)
    repeated = _invoke(dependencies, client_event("PUT", f"{PROMOTION}/PROD", scopes=[PROMOTE]), lambda_context)

    assert first["statusCode"] == 202
    assert first["multiValueHeaders"]["Retry-After"] == ["30"]
    assert _body(first)["stage"] == "PROD"
    assert _body(first)["status"] == "CREATING"
    assert repeated["statusCode"] == 202
    (command,) = _promotions(dependencies)
    assert command.stage.value == "PROD"
    assert command.createdBy.value == "service:client-1"
    assert [role.value for role in command.userRoles] == ["PROGRAM_OWNER"]
    dependencies.project_access_service.require_access.assert_called_with("client-1", "proj-12345")


def test_put_of_a_created_promotion_is_ok_and_runs_nothing(client_event, lambda_context):
    dependencies = _dependencies([_distribution(), _distribution("PROD")])

    response = _invoke(dependencies, client_event("PUT", f"{PROMOTION}/prod", scopes=[PROMOTE]), lambda_context)

    assert response["statusCode"] == 200
    assert _body(response)["status"] == "CREATED"
    assert _promotions(dependencies) == []


def test_get_imports_the_promotion_and_404s_before_it_exists(client_event, lambda_context):
    dependencies = _dependencies([_distribution(), _distribution("PROD")])

    found = _invoke(dependencies, client_event("GET", f"{PROMOTION}/PROD", scopes=[READ]), lambda_context)
    missing = _invoke(dependencies, client_event("GET", f"{PROMOTION}/QA", scopes=[READ]), lambda_context)

    assert found["statusCode"] == 200
    assert _body(found)["distributions"] == [
        {"awsAccountId": "123456789012", "region": "us-east-1", "status": "CREATED"}
    ]
    assert missing["statusCode"] == 404


def test_list_versions_shows_each_version_with_its_stages(client_event, lambda_context):
    dependencies = _dependencies([_distribution(), _distribution("PROD", version.VersionStatus.Creating)])

    response = _invoke(
        dependencies,
        client_event("GET", "/projects/proj-12345/products/prod-12345678/versions", scopes=[READ]),
        lambda_context,
    )

    assert response["statusCode"] == 200
    assert _body(response) == {
        "versions": [
            {
                "versionId": "vers-00000001",
                "versionName": "1.0.0-rc.1",
                "versionType": "RELEASE_CANDIDATE",
                "stages": [{"stage": "DEV", "status": "CREATED"}, {"stage": "PROD", "status": "CREATING"}],
            }
        ]
    }


def test_versions_of_another_programs_product_are_not_found(client_event, lambda_context):
    dependencies = _dependencies([_distribution()])

    response = _invoke(
        dependencies,
        client_event(
            "PUT", "/projects/prog-other/products/prod-12345678/versions/vers-00000001/stages/PROD", scopes=[PROMOTE]
        ),
        lambda_context,
    )

    assert response["statusCode"] == 404
    assert _promotions(dependencies) == []


def test_a_rejected_promotion_is_a_domain_failure(client_event, lambda_context):
    dependencies = _dependencies([_distribution()])
    dependencies.command_bus.handle.side_effect = domain_exception.DomainException(
        "Only release candidate versions can be promoted to PROD"
    )

    response = _invoke(dependencies, client_event("PUT", f"{PROMOTION}/PROD", scopes=[PROMOTE]), lambda_context)

    assert response["statusCode"] == 422
    assert _body(response)["code"] == "DOMAIN_VALIDATION_FAILED"


def test_promotion_needs_the_promote_scope_and_delete_only_forgets(client_event, lambda_context):
    dependencies = _dependencies([_distribution(), _distribution("PROD")])

    read_only = _invoke(dependencies, client_event("PUT", f"{PROMOTION}/PROD", scopes=[READ]), lambda_context)
    deleted = _invoke(dependencies, client_event("DELETE", f"{PROMOTION}/PROD", scopes=[PROMOTE]), lambda_context)

    assert read_only["statusCode"] == 403
    assert deleted["statusCode"] == 204
    assert len(dependencies.versions_query_service.get_product_version_distributions("prod-12345678")) == 2
