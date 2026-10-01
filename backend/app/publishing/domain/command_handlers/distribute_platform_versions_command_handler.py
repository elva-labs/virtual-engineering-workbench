"""Platform versions for a newly onboarded program account (docs/platform-products.md).

A platform product version reaches every program's portfolio at the stage it is created in or
promoted to. A program account onboarded later missed those distributions, so its new portfolio
receives every platform version that is live at its stage, placed the way a promotion places it.
"""

import logging
from datetime import datetime, timezone

from app.publishing.domain.commands import distribute_platform_versions_command
from app.publishing.domain.events import product_version_promotion_started
from app.publishing.domain.model import portfolio, product, version
from app.publishing.domain.ports import products_query_service, versions_query_service
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work

DISTRIBUTOR = "service:platform-distribution"


def handle(
    cmd: distribute_platform_versions_command.DistributePlatformVersionsCommand,
    uow: unit_of_work.UnitOfWork,
    message_bus: message_bus.MessageBus,
    products_qry_srv: products_query_service.ProductsQueryService,
    versions_qry_srv: versions_query_service.VersionsQueryService,
    platform_program_id: str,
    logger: logging.Logger,
) -> int:
    """Returns the number of distributions started."""
    if not platform_program_id:
        return 0
    with uow:
        portf = uow.get_repository(portfolio.PortfolioPrimaryKey, portfolio.Portfolio).get(
            pk=portfolio.PortfolioPrimaryKey(
                technologyId=cmd.technologyId.value, awsAccountId=cmd.awsAccountId.value, stage=cmd.stage.value
            )
        )
    if not portf or portf.status != portfolio.PortfolioStatus.Created or not portf.scPortfolioId:
        logger.info("No ready portfolio for the account and stage; nothing to distribute.")
        return 0

    started = 0
    for prod in products_qry_srv.get_products(project_id=platform_program_id):
        if prod.scope != product.ProductScope.Platform or prod.status != product.ProductStatus.Created:
            continue
        for source in _live_versions_missing_in(versions_qry_srv, prod.productId, portf):
            _distribute(uow, message_bus, prod, source, portf)
            started += 1
    logger.info(f"Started {started} platform version distributions to {portf.awsAccountId} {portf.stage}.")
    return started


def _live_versions_missing_in(
    versions_qry_srv: versions_query_service.VersionsQueryService, product_id: str, portf: portfolio.Portfolio
) -> list[version.Version]:
    """One live distribution per version at the portfolio's stage that the portfolio's account lacks."""
    live: dict[str, version.Version] = {}
    present: set[str] = set()
    for dist in versions_qry_srv.get_product_version_distributions(product_id=product_id):
        if str(dist.stage) != str(portf.stage):
            continue
        if dist.awsAccountId == portf.awsAccountId:
            present.add(dist.versionId)
        elif dist.status == version.VersionStatus.Created:
            live.setdefault(dist.versionId, dist)
    return [source for version_id, source in live.items() if version_id not in present]


def _distribute(
    uow: unit_of_work.UnitOfWork,
    message_bus: message_bus.MessageBus,
    prod: product.Product,
    source: version.Version,
    portf: portfolio.Portfolio,
) -> None:
    current_time = datetime.now(timezone.utc).isoformat()
    entity = version.Version(
        versionId=source.versionId,
        projectId=source.projectId,
        versionName=source.versionName,
        versionType=source.versionType,
        draftTemplateLocation=source.draftTemplateLocation,
        scPortfolioId=portf.scPortfolioId,
        productId=source.productId,
        versionDescription=source.versionDescription,
        technologyId=source.technologyId,
        awsAccountId=portf.awsAccountId,
        accountId=portf.accountId,
        stage=source.stage,
        region=portf.region,
        originalAmiId=source.originalAmiId,
        imageTag=source.imageTag,
        imageDigest=source.imageDigest,
        status=version.VersionStatus.Creating,
        isRecommendedVersion=source.isRecommendedVersion,
        restoredFromVersionName=source.restoredFromVersionName,
        componentVersionDetails=source.componentVersionDetails,
        osVersion=source.osVersion,
        integrations=source.integrations,
        createDate=current_time,
        lastUpdateDate=current_time,
        createdBy=DISTRIBUTOR,
        lastUpdatedBy=DISTRIBUTOR,
    )
    with uow:
        uow.get_repository(version.VersionPrimaryKey, version.Version).add(entity)
        uow.commit()
    message_bus.publish(
        product_version_promotion_started.ProductVersionPromotionStarted(
            product_id=entity.productId,
            version_id=entity.versionId,
            aws_account_id=entity.awsAccountId,
            stage=str(entity.stage),
            product_type=prod.productType,
        )
    )
