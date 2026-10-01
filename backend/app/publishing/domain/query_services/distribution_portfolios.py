"""The portfolios a product version is distributed to (docs/platform-products.md).

A program product goes to its own technology's portfolios, as upstream VEW does. A platform product
goes to every program's portfolio at the stage, so one build, one test and one promotion by the
releasing program reach every program's accounts.
"""

import typing

from app.publishing.domain.model import portfolio, product
from app.publishing.domain.ports import portfolios_query_service


def target_portfolios(
    portf_qry_srv: portfolios_query_service.PortfoliosQueryService, product_entity: product.Product, stage: str
) -> typing.List[portfolio.Portfolio]:
    stage = str(stage)
    if product_entity.scope == product.ProductScope.Platform:
        return [
            portf
            for portf in portf_qry_srv.get_portfolios_by_stage(stage)
            if portf.status == portfolio.PortfolioStatus.Created and portf.scPortfolioId
        ]
    return portf_qry_srv.get_portfolios_by_tech_and_stage(product_entity.technologyId, stage)
