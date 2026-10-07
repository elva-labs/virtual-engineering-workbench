"""Reads a spoke's quotas and what uses them, and files quota increase requests.

Everything goes through the spoke's provisioning role , which the spoke app
grants the read-only Service Quotas and EC2 describe calls plus RequestServiceQuotaIncrease.
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional

from app.provisioning.domain.model import spoke_capacity

# A session name the role's trust admits (it requires a UserId session tag); not a person.
SERVICE_USER_ID = "VEW-CAPACITY"
QUOTA_CACHE_SECONDS = 3600
WORKBENCH_TAG = "vew:provisionedProduct:productType"

ClientProvider = Callable[[str, str, str], object]


class AWSSpokeCapacityService:
    def __init__(
        self,
        servicequotas_client_provider: ClientProvider,
        ec2_client_provider: ClientProvider,
        logger: logging.Logger,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._sq = servicequotas_client_provider
        self._ec2 = ec2_client_provider
        self._logger = logger
        self._clock = clock
        self._quota_cache: dict[tuple, tuple[float, Optional[float]]] = {}

    def read(
        self, config: spoke_capacity.CapacityConfig, aws_account_id: str, region: str
    ) -> spoke_capacity.SpokeCapacity:
        now = datetime.now(timezone.utc).isoformat()
        try:
            ec2 = self._ec2(aws_account_id, region, SERVICE_USER_ID)
            instances = self._instances(ec2)
            vcpus_by_type = self._vcpus_by_type(ec2, {i.instanceType for i in instances})
            for instance in instances:
                instance.vcpus = (
                    vcpus_by_type.get(instance.instanceType) or spoke_capacity.vcpus_of(instance.instanceType) or 0
                )
            gp3_gib = self._volume_gib(ec2, "gp3")
        except Exception as error:  # noqa: BLE001 - one spoke must not stop the others
            self._logger.warning("capacity: reading EC2 in %s/%s failed: %s", aws_account_id, region, error)
            return spoke_capacity.SpokeCapacity(
                awsAccountId=aws_account_id, region=region, collectedAt=now, error=f"EC2: {error}"
            )
        used_vcpus = spoke_capacity.vcpu_usage(config, instances)
        quotas = []
        errors = []
        for quota in config.quotas:
            limit, error = self._quota_value(aws_account_id, region, quota)
            if error:
                errors.append(error)
            used = (
                used_vcpus.get(quota.quotaCode, 0.0)
                if quota.serviceCode == "ec2"
                else gp3_gib / spoke_capacity.GIB_PER_TIB
            )
            quotas.append(
                spoke_capacity.QuotaUsage(
                    serviceCode=quota.serviceCode,
                    quotaCode=quota.quotaCode,
                    label=quota.label,
                    unit=quota.unit,
                    limit=limit,
                    used=round(used, 3),
                    instanceFamilies=quota.instanceFamilies,
                )
            )
        return spoke_capacity.SpokeCapacity(
            awsAccountId=aws_account_id,
            region=region,
            collectedAt=now,
            quotas=quotas,
            instances=instances,
            vcpusByType=vcpus_by_type,
            gp3GiB=gp3_gib,
            error="; ".join(errors) or None,
        )

    def request_increase(
        self, aws_account_id: str, region: str, service_code: str, quota_code: str, desired_value: float
    ) -> dict:
        """RequestServiceQuotaIncrease; an already open request for the quota is returned instead."""
        client = self._sq(aws_account_id, region, SERVICE_USER_ID)
        try:
            return client.request_service_quota_increase(
                ServiceCode=service_code, QuotaCode=quota_code, DesiredValue=float(desired_value)
            )["RequestedQuota"]
        except client.exceptions.ResourceAlreadyExistsException:
            open_requests = [
                r
                for r in client.list_requested_service_quota_change_history_by_quota(
                    ServiceCode=service_code, QuotaCode=quota_code
                ).get("RequestedQuotas", [])
                if r.get("Status") in spoke_capacity.QuotaRequestStatus.open_values()
            ]
            if not open_requests:
                raise
            return open_requests[0]

    def request_status(self, aws_account_id: str, region: str, request_id: str) -> dict:
        client = self._sq(aws_account_id, region, SERVICE_USER_ID)
        return client.get_requested_service_quota_change(RequestId=request_id)["RequestedQuota"]

    def _quota_value(self, aws_account_id: str, region: str, quota: spoke_capacity.QuotaConfig):
        key = (aws_account_id, region, quota.quotaCode)
        cached = self._quota_cache.get(key)
        if cached and self._clock() - cached[0] < QUOTA_CACHE_SECONDS:
            return cached[1], None
        try:
            client = self._sq(aws_account_id, region, SERVICE_USER_ID)
            value = client.get_service_quota(ServiceCode=quota.serviceCode, QuotaCode=quota.quotaCode)["Quota"]["Value"]
        except Exception as error:  # noqa: BLE001 - an unreadable quota is reported, not fatal
            self._logger.warning(
                "capacity: quota %s in %s/%s unreadable: %s", quota.quotaCode, aws_account_id, region, error
            )
            return None, f"{quota.quotaCode}: {error}"
        self._quota_cache[key] = (self._clock(), float(value))
        return float(value), None

    @staticmethod
    def _instances(ec2) -> list[spoke_capacity.InstanceCount]:
        counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        for page in ec2.get_paginator("describe_instances").paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
        ):
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    # Spot instances count against the Spot quotas, not the On-Demand ones.
                    if instance.get("InstanceLifecycle") == "spot":
                        continue
                    key = (instance["InstanceType"], instance["State"]["Name"])
                    counts[key][0] += 1
                    if any(t.get("Key") == WORKBENCH_TAG for t in instance.get("Tags", [])):
                        counts[key][1] += 1
        return [
            spoke_capacity.InstanceCount(instanceType=it, state=state, count=c[0], workbenches=c[1], vcpus=0)
            for (it, state), c in sorted(counts.items())
        ]

    @staticmethod
    def _vcpus_by_type(ec2, instance_types: set[str]) -> dict[str, int]:
        if not instance_types:
            return {}
        result = {}
        for page in ec2.get_paginator("describe_instance_types").paginate(InstanceTypes=sorted(instance_types)):
            for it in page.get("InstanceTypes", []):
                result[it["InstanceType"]] = it["VCpuInfo"]["DefaultVCpus"]
        return result

    @staticmethod
    def _volume_gib(ec2, volume_type: str) -> int:
        total = 0
        for page in ec2.get_paginator("describe_volumes").paginate(
            Filters=[{"Name": "volume-type", "Values": [volume_type]}]
        ):
            total += sum(v.get("Size", 0) for v in page.get("Volumes", []))
        return total
