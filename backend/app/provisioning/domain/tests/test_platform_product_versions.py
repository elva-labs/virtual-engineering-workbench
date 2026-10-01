"""Platform product versions (docs/platform-products.md): a program sees the distributions in its own accounts."""

from unittest import mock

from app.provisioning.domain.ports import products_query_service, versions_query_service
from app.provisioning.domain.query_services import versions_domain_query_service
from app.provisioning.domain.read_models import product, version
from app.provisioning.domain.value_objects import product_id_value_object

PLATFORM = "prog-73488"


def _version(account, stage=version.VersionStage.PROD, project_id=PLATFORM):
    return version.Version(
        projectId=project_id,
        productId="prod-default1",
        technologyId="tech-platform",
        versionId="vers-1",
        versionName="1.0.0",
        awsAccountId=account,
        accountId=f"acc-{account}",
        stage=stage,
        region="eu-north-1",
        amiId="ami-1",
        scProductId="sc-prod-1",
        scProvisioningArtifactId="sc-pa-1",
        isRecommendedVersion=True,
        parameters=[],
        lastUpdateDate="2026-10-01",
    )


def _service(owner_project_id):
    versions = mock.create_autospec(spec=versions_query_service.VersionsQueryService)
    versions.get_product_version_distributions.return_value = [
        _version("111111111111"),
        _version("222222222222"),
        _version("111111111111", stage=version.VersionStage.DEV),
    ]
    products = mock.create_autospec(spec=products_query_service.ProductsQueryService)
    products.get_product.return_value = mock.Mock(projectId=owner_project_id, scope=product.ProductScope.Platform)
    accounts = {"proj-a": {"111111111111"}, "proj-b": {"222222222222"}}
    return versions_domain_query_service.VersionsDomainQueryService(
        version_qry_srv=versions,
        products_qry_srv=products,
        program_aws_account_ids=lambda project_id: accounts.get(project_id, set()),
    )


def _listed(service, project_id, roles=None):
    return service.get_versions_ready_for_provisioning(
        product_id=product_id_value_object.from_str("prod-default1"),
        stage=None,
        region=None,
        user_roles=roles,
        project_id=project_id,
    )


def test_a_program_sees_the_platform_versions_in_its_own_accounts():
    listed = _listed(_service(PLATFORM), "proj-a")

    assert sorted((v.awsAccountId, str(v.stage)) for v in listed) == [("111111111111", "DEV"), ("111111111111", "PROD")]


def test_stage_access_still_applies_to_platform_versions():
    listed = _listed(_service(PLATFORM), "proj-a", roles=["PLATFORM_USER"])

    assert [str(v.stage) for v in listed] == ["PROD"]


def test_a_program_without_accounts_sees_no_platform_versions():
    assert _listed(_service(PLATFORM), "proj-new") == []
