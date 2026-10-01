"""Platform products (docs/platform-products.md): one release, distributed to every program's accounts."""

import logging
from unittest import mock

from app.publishing.domain.command_handlers import distribute_platform_versions_command_handler
from app.publishing.domain.commands import distribute_platform_versions_command
from app.publishing.domain.events import product_version_promotion_started
from app.publishing.domain.model import portfolio, product, version
from app.publishing.domain.ports import portfolios_query_service, products_query_service, versions_query_service
from app.publishing.domain.query_services import distribution_portfolios
from app.publishing.domain.value_objects import aws_account_id_value_object, stage_value_object, tech_id_value_object

NOW = "2026-10-01T00:00:00+00:00"


def _product(scope=product.ProductScope.Program, project_id="prog-73488"):
    return product.Product(
        projectId=project_id,
        productId="prod-default1",
        technologyId="tech-platform",
        technologyName="Saab default",
        status=product.ProductStatus.Created,
        productName="Saab default workbench",
        productType=product.ProductType.Workbench,
        createDate=NOW,
        lastUpdateDate=NOW,
        createdBy="service:client-1",
        lastUpdatedBy="service:client-1",
        scope=scope,
    )


def _portfolio(tech, account, stage="DEV", status=portfolio.PortfolioStatus.Created, project_id="proj-a"):
    return portfolio.Portfolio(
        projectId=project_id,
        technologyId=tech,
        awsAccountId=account,
        accountId=f"acc-{account}",
        stage=portfolio.PortfolioStage(stage),
        region="eu-north-1",
        status=status,
        scPortfolioId=f"port-{account}-{stage}".lower(),
        createDate=NOW,
        lastUpdateDate=NOW,
    )


def _version(account, stage="PROD", status=version.VersionStatus.Created, version_id="vers-1"):
    return version.Version(
        projectId="prog-73488",
        productId="prod-default1",
        technologyId="tech-platform",
        versionId=version_id,
        versionName="1.0.0",
        versionType="RELEASED",
        awsAccountId=account,
        accountId=f"acc-{account}",
        stage=version.VersionStage(stage),
        region="eu-north-1",
        originalAmiId="ami-0123",
        status=status,
        scPortfolioId=f"port-{account}",
        isRecommendedVersion=False,
        draftTemplateLocation="s3://templates/draft.yml",
        createDate=NOW,
        lastUpdateDate=NOW,
        createdBy="service:client-1",
        lastUpdatedBy="service:client-1",
    )


def test_a_program_product_goes_to_its_own_technology():
    portfolios = mock.create_autospec(portfolios_query_service.PortfoliosQueryService, instance=True)
    portfolios.get_portfolios_by_tech_and_stage.return_value = [_portfolio("tech-platform", "111111111111")]

    found = distribution_portfolios.target_portfolios(portfolios, _product(), "DEV")

    assert [p.awsAccountId for p in found] == ["111111111111"]
    portfolios.get_portfolios_by_tech_and_stage.assert_called_once_with("tech-platform", "DEV")
    portfolios.get_portfolios_by_stage.assert_not_called()


def test_a_platform_product_goes_to_every_ready_portfolio_at_the_stage():
    portfolios = mock.create_autospec(portfolios_query_service.PortfoliosQueryService, instance=True)
    portfolios.get_portfolios_by_stage.return_value = [
        _portfolio("tech-a", "111111111111"),
        _portfolio("tech-b", "222222222222", project_id="proj-b"),
        _portfolio("tech-c", "333333333333", status=portfolio.PortfolioStatus.Creating),
    ]

    found = distribution_portfolios.target_portfolios(portfolios, _product(product.ProductScope.Platform), "DEV")

    assert [p.awsAccountId for p in found] == ["111111111111", "222222222222"]
    portfolios.get_portfolios_by_tech_and_stage.assert_not_called()


def _distribute(portf, products, distributions, platform_program_id="prog-73488"):
    uow = mock.MagicMock()
    uow.get_repository.return_value.get.return_value = portf
    bus = mock.MagicMock()
    products_qry = mock.create_autospec(products_query_service.ProductsQueryService, instance=True)
    products_qry.get_products.return_value = products
    versions_qry = mock.create_autospec(versions_query_service.VersionsQueryService, instance=True)
    versions_qry.get_product_version_distributions.return_value = distributions
    started = distribute_platform_versions_command_handler.handle(
        cmd=distribute_platform_versions_command.DistributePlatformVersionsCommand(
            technologyId=tech_id_value_object.from_str("tech-new"),
            awsAccountId=aws_account_id_value_object.from_str("444444444444"),
            stage=stage_value_object.from_str("PROD"),
        ),
        uow=uow,
        message_bus=bus,
        products_qry_srv=products_qry,
        versions_qry_srv=versions_qry,
        platform_program_id=platform_program_id,
        logger=logging.getLogger("test"),
    )
    return started, uow, bus


def test_a_new_account_receives_the_live_platform_versions_at_its_stage():
    portf = _portfolio("tech-new", "444444444444", stage="PROD", project_id="proj-new")
    distributions = [
        _version("111111111111"),
        _version("222222222222"),  # same version in another program: distributed once
        _version("111111111111", stage="DEV", version_id="vers-2"),  # another stage
        _version("111111111111", status=version.VersionStatus.Retired, version_id="vers-3"),  # not live
    ]

    started, uow, bus = _distribute(portf, [_product(product.ProductScope.Platform)], distributions)

    assert started == 1
    added = uow.get_repository.return_value.add.call_args.args[0]
    assert (added.versionId, added.awsAccountId, added.accountId) == ("vers-1", "444444444444", "acc-444444444444")
    assert added.scPortfolioId == portf.scPortfolioId
    assert added.status == version.VersionStatus.Creating
    (event,) = [c.args[0] for c in bus.publish.call_args_list]
    assert isinstance(event, product_version_promotion_started.ProductVersionPromotionStarted)
    assert (event.aws_account_id, event.stage) == ("444444444444", "PROD")


def test_an_account_that_has_the_version_already_gets_nothing():
    portf = _portfolio("tech-new", "444444444444", stage="PROD")
    started, uow, _ = _distribute(
        portf, [_product(product.ProductScope.Platform)], [_version("111111111111"), _version("444444444444")]
    )

    assert started == 0
    uow.get_repository.return_value.add.assert_not_called()


def test_program_products_and_unconfigured_deployments_are_left_alone():
    portf = _portfolio("tech-new", "444444444444", stage="PROD")

    assert _distribute(portf, [_product()], [_version("111111111111")])[0] == 0
    assert _distribute(portf, [_product(product.ProductScope.Platform)], [_version("1" * 12)], "")[0] == 0


def test_a_portfolio_that_is_not_ready_waits():
    portf = _portfolio("tech-new", "444444444444", stage="PROD", status=portfolio.PortfolioStatus.Creating)

    assert _distribute(portf, [_product(product.ProductScope.Platform)], [_version("111111111111")])[0] == 0
