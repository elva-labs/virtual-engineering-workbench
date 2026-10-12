from abc import ABC, abstractmethod


class VersionWorkbenchesQueryService(ABC):
    """The workbenches still on a product version, in every project."""

    @abstractmethod
    def get_active_workbench_ids(self, product_id: str, version_id: str) -> list[str]: ...
