"""Reads a workbench's idle signals from CloudWatch in its workbench account.

The client comes from the provisioning role the scheduled jobs already assume in each workbench
account, which may call cloudwatch:GetMetricData.
"""

from datetime import datetime
from typing import Callable

from app.provisioning.domain.model import idle_signals
from app.provisioning.domain.ports import workbench_signals_service

SESSION_NAME = "VEWProvisioningBCIdleStop"


class CloudWatchWorkbenchSignalsService(workbench_signals_service.WorkbenchSignalsService):
    def __init__(self, cloudwatch_boto_client_provider: Callable):
        self._client_provider = cloudwatch_boto_client_provider

    def get_signals(
        self,
        aws_account_id: str,
        region: str,
        instance_id: str,
        metric_names: list[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[tuple[datetime, float]]]:
        client = self._client_provider(aws_account_id, region, SESSION_NAME)
        queries = [
            {
                "Id": f"m{index}",
                "Label": name,
                "MetricStat": {
                    "Metric": {
                        "Namespace": idle_signals.METRICS_NAMESPACE,
                        "MetricName": name,
                        "Dimensions": [{"Name": idle_signals.DIMENSION, "Value": instance_id}],
                    },
                    "Period": idle_signals.PERIOD_SECONDS,
                    # Maximum: one busy sample in a period makes the period busy.
                    "Stat": "Maximum",
                },
                "ReturnData": True,
            }
            for index, name in enumerate(metric_names)
        ]
        result: dict[str, list[tuple[datetime, float]]] = {name: [] for name in metric_names}
        token = None
        while True:
            kwargs = {
                "MetricDataQueries": queries,
                "StartTime": start,
                "EndTime": end,
                "ScanBy": "TimestampAscending",
            }
            if token:
                kwargs["NextToken"] = token
            response = client.get_metric_data(**kwargs)
            for series in response.get("MetricDataResults", []):
                points = list(zip(series.get("Timestamps", []), series.get("Values", [])))
                result.setdefault(series["Label"], []).extend(points)
            token = response.get("NextToken")
            if not token:
                break
        for name in result:
            result[name].sort(key=lambda point: point[0])
        return result
