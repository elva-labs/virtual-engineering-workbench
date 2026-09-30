import typing
from abc import ABC, abstractmethod

from app.publishing.domain.read_models import technology


class TechnologiesQueryService(ABC):
    @abstractmethod
    def get_technology(self, project_id: str, technology_id: str) -> typing.Optional[technology.Technology]: ...
