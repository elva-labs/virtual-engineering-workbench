import typing
from abc import ABC, abstractmethod

from app.publishing.domain.model import portfolio


class PortfoliosQueryService(ABC):
    @abstractmethod
    def get_portfolios_by_tech_and_stage(
        self, technology_id: str, portfolio_stage: str
    ) -> typing.List[portfolio.Portfolio]: ...

    @abstractmethod
    def get_portfolios_by_stage(self, portfolio_stage: str) -> typing.List[portfolio.Portfolio]:
        """Every program's portfolio at a stage: where platform product versions go (docs/platform-products.md)."""
        ...
