from abc import ABC, abstractmethod
from datetime import datetime


class WorkbenchSignalsService(ABC):
    """The idle signals a workbench's agent reports, read from its workbench account."""

    @abstractmethod
    def get_signals(
        self,
        aws_account_id: str,
        region: str,
        instance_id: str,
        metric_names: list[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[tuple[datetime, float]]]:
        """5-minute maxima per metric name, oldest first; a metric without data maps to []."""
