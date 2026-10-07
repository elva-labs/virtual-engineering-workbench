"""Spoke capacity: the service quotas of a spoke account and what uses them.

The hub reads every enrolled spoke's EC2 vCPU quotas (Running On-Demand Standard, G and VT, P) and its
gp3 storage quota, plus the running instances and gp3 volumes that count against them. A launch that
would not fit is refused before Service Catalog is called; platform admins see the numbers and can
request more. Quotas are per account and region; one account serves one program.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Literal, Optional

from pydantic import BaseModel, Field

GIB_PER_TIB = 1024

# Running On-Demand quotas count instances that run or are about to (AWS: "running" vCPU usage).
COUNTED_STATES = ("pending", "running")

_SIZE_VCPUS = {
    "nano": 2,
    "micro": 2,
    "small": 2,
    "medium": 2,
    "large": 2,
    "xlarge": 4,
}


class QuotaConfig(BaseModel):
    serviceCode: Literal["ec2", "ebs"]
    quotaCode: str
    label: str
    instanceFamilies: list[str] = Field(default_factory=list)
    volumeType: Optional[str] = None

    @property
    def unit(self) -> str:
        return "TiB" if self.serviceCode == "ebs" else "vCPU"


class CapacityConfig(BaseModel):
    """SPOKE_CAPACITY: the deployment's "spoke-capacity" config (infra/config.py)."""

    enabled: bool = False
    headroomVcpus: int = 0
    alarmUsedPercent: int = 80
    staleAfterMinutes: int = 30
    quotas: list[QuotaConfig] = Field(default_factory=list)

    @classmethod
    def from_env(cls) -> "CapacityConfig":
        return cls.model_validate(json.loads(os.environ.get("SPOKE_CAPACITY") or "{}"))

    def quota_for_instance_type(self, instance_type: str) -> Optional[QuotaConfig]:
        family = instance_family(instance_type)
        return next((q for q in self.quotas if q.serviceCode == "ec2" and family in q.instanceFamilies), None)

    def storage_quota(self, volume_type: str = "gp3") -> Optional[QuotaConfig]:
        return next((q for q in self.quotas if q.serviceCode == "ebs" and q.volumeType == volume_type), None)


class QuotaUsage(BaseModel):
    serviceCode: str
    quotaCode: str
    label: str
    unit: str
    # None when the quota could not be read (the launch check then does not refuse on it).
    limit: Optional[float] = None
    used: float = 0
    instanceFamilies: list[str] = Field(default_factory=list)

    @property
    def remaining(self) -> Optional[float]:
        return None if self.limit is None else self.limit - self.used

    @property
    def used_percent(self) -> Optional[float]:
        if self.limit is None:
            return None
        if self.limit <= 0:
            return 100.0 if self.used > 0 else 0.0
        return round(100.0 * self.used / self.limit, 1)


class InstanceCount(BaseModel):
    instanceType: str
    state: str
    count: int
    vcpus: int
    # Instances that are VEW workbenches (tag vew:provisionedProduct:productType); the rest is the
    # account's other use of the same quota (a jumphost, a team's own EC2).
    workbenches: int = 0


class ProgramRef(BaseModel):
    projectId: str
    projectName: Optional[str] = None
    stages: list[str] = Field(default_factory=list)


class SpokeCapacity(BaseModel):
    awsAccountId: str
    region: str
    programs: list[ProgramRef] = Field(default_factory=list)
    collectedAt: str
    quotas: list[QuotaUsage] = Field(default_factory=list)
    instances: list[InstanceCount] = Field(default_factory=list)
    vcpusByType: dict[str, int] = Field(default_factory=dict)
    gp3GiB: int = 0
    error: Optional[str] = None

    def quota(self, quota_code: str) -> Optional[QuotaUsage]:
        return next((q for q in self.quotas if q.quotaCode == quota_code), None)

    def age(self, now: datetime) -> timedelta:
        return now - datetime.fromisoformat(self.collectedAt)


class QuotaRequestStatus(StrEnum):
    # Service Quotas' RequestStatus values; the first two are still open.
    Pending = "PENDING"
    CaseOpened = "CASE_OPENED"
    Approved = "APPROVED"
    Denied = "DENIED"
    CaseClosed = "CASE_CLOSED"
    NotApproved = "NOT_APPROVED"
    Invalid = "INVALID_REQUEST"

    @classmethod
    def open_values(cls) -> set[str]:
        return {cls.Pending.value, cls.CaseOpened.value}


class QuotaIncreaseRequest(BaseModel):
    awsAccountId: str
    region: str
    serviceCode: str
    quotaCode: str
    desiredValue: float
    requestedBy: str
    requestedAt: str
    requestId: Optional[str] = None
    caseId: Optional[str] = None
    status: str = QuotaRequestStatus.Pending.value
    updatedAt: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.status in QuotaRequestStatus.open_values()


class FitResult(BaseModel):
    fits: bool
    reason: Optional[str] = None
    quotaCode: Optional[str] = None
    quotaLabel: Optional[str] = None
    required: Optional[float] = None
    remaining: Optional[float] = None


class CapacityExceeded(Exception):
    """A launch does not fit the spoke's remaining quota."""

    def __init__(self, result: FitResult):
        super().__init__(result.reason)
        self.result = result


def instance_family(instance_type: str) -> str:
    """The quota family of an instance type: its leading letters ("g6.xlarge" -> "g", "vt1..." -> "vt")."""
    match = re.match(r"^([a-z]+)", (instance_type or "").lower())
    return match.group(1) if match else ""


def vcpus_of(instance_type: str, known: dict[str, int] | None = None) -> Optional[int]:
    """vCPUs of an instance type: what EC2 reported for it, else from its size (x86 and Graviton
    families share the rule: xlarge = 4, Nxlarge = 4N). None for sizes the rule does not cover."""
    if known and instance_type in known:
        return known[instance_type]
    size = (instance_type or "").split(".")[-1]
    if size in _SIZE_VCPUS:
        return _SIZE_VCPUS[size]
    match = re.match(r"^([0-9]+)xlarge$", size)
    return 4 * int(match.group(1)) if match else None


def vcpu_usage(config: CapacityConfig, instances: list[InstanceCount]) -> dict[str, float]:
    """vCPUs in use per EC2 quota code, from the counted (pending, running) instances."""
    used: dict[str, float] = {q.quotaCode: 0.0 for q in config.quotas if q.serviceCode == "ec2"}
    for instance in instances:
        if instance.state not in COUNTED_STATES:
            continue
        quota = config.quota_for_instance_type(instance.instanceType)
        if quota is not None:
            used[quota.quotaCode] += instance.vcpus * instance.count
    return used


def check_launch(
    config: CapacityConfig,
    snapshot: Optional[SpokeCapacity],
    instance_type: Optional[str],
    volume_gib: Optional[int],
    now: datetime | None = None,
) -> FitResult:
    """Whether a workbench of this size fits the spoke. Fails open: no snapshot, a stale one, an
    unreadable quota or an unknown size never refuse (EC2 itself still enforces the quota)."""
    now = now or datetime.now(timezone.utc)
    if not config.enabled or snapshot is None:
        return FitResult(fits=True, reason="no capacity data")
    if snapshot.age(now) > timedelta(minutes=config.staleAfterMinutes):
        return FitResult(fits=True, reason="capacity data is stale")
    if instance_type:
        result = _check_vcpus(config, snapshot, instance_type)
        if not result.fits:
            return result
    if volume_gib:
        result = _check_storage(config, snapshot, volume_gib)
        if not result.fits:
            return result
    return FitResult(fits=True)


def _check_vcpus(config: CapacityConfig, snapshot: SpokeCapacity, instance_type: str) -> FitResult:
    quota_cfg = config.quota_for_instance_type(instance_type)
    vcpus = vcpus_of(instance_type, snapshot.vcpusByType)
    usage = snapshot.quota(quota_cfg.quotaCode) if quota_cfg else None
    if usage is None or usage.remaining is None or vcpus is None:
        return FitResult(fits=True)
    remaining = usage.remaining - config.headroomVcpus
    if vcpus <= remaining:
        return FitResult(fits=True, quotaCode=usage.quotaCode, required=vcpus, remaining=remaining)
    gpu = instance_family(instance_type) in ("g", "vt", "p")
    what = "GPU capacity" if gpu else "Capacity"
    return FitResult(
        fits=False,
        quotaCode=usage.quotaCode,
        quotaLabel=usage.label,
        required=vcpus,
        remaining=max(remaining, 0),
        reason=(
            f"{what} in this program's account is used up: {instance_type} needs {vcpus} vCPU, "
            f"{max(int(remaining), 0)} of {int(usage.limit or 0)} vCPU ({usage.label}) are free. "
            "Choose a smaller size, stop a workbench, or ask a platform admin to request more."
        ),
    )


def _check_storage(config: CapacityConfig, snapshot: SpokeCapacity, volume_gib: int) -> FitResult:
    quota_cfg = config.storage_quota()
    usage = snapshot.quota(quota_cfg.quotaCode) if quota_cfg else None
    if usage is None or usage.remaining is None:
        return FitResult(fits=True)
    required = volume_gib / GIB_PER_TIB
    if required <= usage.remaining:
        return FitResult(fits=True, quotaCode=usage.quotaCode, required=required, remaining=usage.remaining)
    return FitResult(
        fits=False,
        quotaCode=usage.quotaCode,
        quotaLabel=usage.label,
        required=required,
        remaining=max(usage.remaining, 0),
        reason=(
            f"Storage in this program's account is used up: {volume_gib} GB do not fit the "
            f"{usage.label} quota ({usage.used:.1f} of {usage.limit:.0f} TiB used). "
            "Ask a platform admin to request more."
        ),
    )


def available_per_size(
    config: CapacityConfig, snapshot: Optional[SpokeCapacity], instance_types: list[str]
) -> dict[str, Optional[int]]:
    """For the launch form: how many more workbenches of each size fit (None = no data)."""
    result: dict[str, Optional[int]] = {}
    for instance_type in instance_types:
        quota_cfg = config.quota_for_instance_type(instance_type)
        usage = snapshot.quota(quota_cfg.quotaCode) if (snapshot and quota_cfg) else None
        vcpus = vcpus_of(instance_type, snapshot.vcpusByType if snapshot else None)
        if usage is None or usage.remaining is None or not vcpus:
            result[instance_type] = None
            continue
        result[instance_type] = max(int((usage.remaining - config.headroomVcpus) // vcpus), 0)
    return result
