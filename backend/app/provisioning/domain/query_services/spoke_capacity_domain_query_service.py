"""Spoke capacity: collect, check a launch, summarise for admins, request more quota."""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from app.provisioning.domain.model import product_status, spoke_capacity

ACTIVE_ACCOUNT_STATUSES = ["Active"]


class QuotaAlreadySufficient(Exception):
    pass


class UnknownQuota(Exception):
    pass


class SpokeCapacityDomainQueryService:
    def __init__(
        self,
        config: spoke_capacity.CapacityConfig,
        store,
        reader,
        projects_qry_srv,
        logger: logging.Logger,
        pp_qry_srv=None,
        default_region: str | None = None,
    ):
        self._config = config
        self._store = store
        self._reader = reader
        self._projects = projects_qry_srv
        self._pp = pp_qry_srv
        self._logger = logger
        self._default_region = default_region

    @property
    def config(self) -> spoke_capacity.CapacityConfig:
        return self._config

    # --- collector -------------------------------------------------------------------------------

    def spokes(self) -> dict[tuple[str, str], list[spoke_capacity.ProgramRef]]:
        """(account, region) -> the programs whose active account records point at it."""
        spokes: dict[tuple[str, str], dict[str, spoke_capacity.ProgramRef]] = defaultdict(dict)
        for project in self._projects.get_projects():
            try:
                accounts = self._projects.get_aws_accounts_by_status(
                    project_id=project.projectId, statuses=ACTIVE_ACCOUNT_STATUSES
                )
            except Exception as error:  # noqa: BLE001 - a program without accounts has none
                self._logger.info("capacity: no accounts for %s: %s", project.projectId, error)
                continue
            for account in accounts:
                key = (account.awsAccountId, account.region or self._default_region)
                ref = spokes[key].setdefault(
                    project.projectId,
                    spoke_capacity.ProgramRef(projectId=project.projectId, projectName=project.projectName),
                )
                if account.stage and account.stage not in ref.stages:
                    ref.stages.append(account.stage)
        return {key: list(refs.values()) for key, refs in spokes.items()}

    def collect(self) -> list[spoke_capacity.SpokeCapacity]:
        if not self._config.enabled:
            return []
        snapshots = []
        for (aws_account_id, region), programs in sorted(self.spokes().items()):
            snapshot = self._reader.read(self._config, aws_account_id, region)
            snapshot.programs = programs
            self._store.put_snapshot(snapshot)
            snapshots.append(snapshot)
        self.refresh_requests()
        return snapshots

    def refresh_requests(self) -> None:
        for request in self._store.list_requests():
            if not request.is_open or not request.requestId:
                continue
            try:
                current = self._reader.request_status(request.awsAccountId, request.region, request.requestId)
            except Exception as error:  # noqa: BLE001 - retried next run
                self._logger.warning("capacity: status of request %s unreadable: %s", request.requestId, error)
                continue
            if current.get("Status") != request.status or current.get("CaseId") != request.caseId:
                request.status = current.get("Status", request.status)
                request.caseId = current.get("CaseId", request.caseId)
                request.updatedAt = datetime.now(timezone.utc).isoformat()
                self._store.put_request(request)

    # --- launch check ----------------------------------------------------------------------------

    def check_launch(self, aws_account_id: str, region: str, instance_type: str | None, volume_gib: int | None):
        """Raises CapacityExceeded when the workbench would not fit; fails open otherwise."""
        if not self._config.enabled:
            return spoke_capacity.FitResult(fits=True)
        try:
            snapshot = self._store.get_snapshot(aws_account_id, region)
        except Exception as error:  # noqa: BLE001 - never block a launch on our own read
            self._logger.warning("capacity: snapshot of %s/%s unreadable: %s", aws_account_id, region, error)
            return spoke_capacity.FitResult(fits=True, reason="capacity data unreadable")
        result = spoke_capacity.check_launch(self._config, snapshot, instance_type, volume_gib)
        if not result.fits:
            raise spoke_capacity.CapacityExceeded(result)
        return result

    # --- views -----------------------------------------------------------------------------------

    def overview(self) -> dict:
        snapshots = self._store.list_snapshots()
        requests = self._store.list_requests()
        return {
            "collectedAt": max((s.collectedAt for s in snapshots), default=None),
            "alarmUsedPercent": self._config.alarmUsedPercent,
            "totals": _totals(snapshots),
            "accounts": [_account_view(s, [r for r in requests if r.awsAccountId == s.awsAccountId]) for s in snapshots],
        }

    def project_capacity(self, project_id: str, instance_types: list[str]) -> dict:
        """For the launch form: per account of the program, how many workbenches of each size fit."""
        accounts = []
        for snapshot in self._store.list_snapshots():
            if not any(p.projectId == project_id for p in snapshot.programs):
                continue
            accounts.append(
                {
                    "awsAccountId": snapshot.awsAccountId,
                    "region": snapshot.region,
                    "collectedAt": snapshot.collectedAt,
                    "available": spoke_capacity.available_per_size(self._config, snapshot, instance_types),
                    "quotas": [_quota_view(q) for q in snapshot.quotas],
                }
            )
        return {"projectId": project_id, "accounts": accounts}

    def project_workbenches(self, project_id: str) -> dict:
        """Admin drill-down: every workbench of a program with its size, version and state."""
        pps = self._pp.get_provisioned_products_by_project_id(
            project_id=project_id, exclude_status=[product_status.ProductStatus.Terminated]
        )
        rows = []
        for pp in pps:
            params = {p.key: p.value for p in (pp.provisioningParameters or [])}
            rows.append(
                {
                    "provisionedProductId": pp.provisionedProductId,
                    "owner": pp.ownerEmail or pp.userId,
                    "productName": pp.productName,
                    "versionName": pp.versionName,
                    "stage": pp.stage,
                    "awsAccountId": pp.awsAccountId,
                    "region": pp.region,
                    "status": pp.status,
                    "instanceType": params.get("InstanceType"),
                    "volumeSize": params.get("VolumeSize"),
                    "startDate": pp.startDate,
                    "lastUpdateDate": pp.lastUpdateDate,
                    "idleTimeoutMinutes": (pp.lifecycleSettings.idleTimeoutMinutes if pp.lifecycleSettings else None),
                    "nightlyStopDisabled": (pp.lifecycleSettings.nightlyStopDisabled if pp.lifecycleSettings else False),
                }
            )
        by_type: dict[str, int] = defaultdict(int)
        for row in rows:
            if row["status"] == product_status.ProductStatus.Running:
                by_type[row["instanceType"] or "unknown"] += 1
        return {"projectId": project_id, "workbenches": rows, "runningByInstanceType": dict(by_type)}

    # --- quota increase --------------------------------------------------------------------------

    def request_increase(
        self, aws_account_id: str, region: str, quota_code: str, desired_value: float, requested_by: str
    ) -> spoke_capacity.QuotaIncreaseRequest:
        """Idempotent: an open request for the quota that asks for at least as much is returned."""
        quota = next((q for q in self._config.quotas if q.quotaCode == quota_code), None)
        if quota is None:
            raise UnknownQuota(f"Quota {quota_code} is not one VEW watches.")
        snapshot = self._store.get_snapshot(aws_account_id, region)
        usage = snapshot.quota(quota_code) if snapshot else None
        if usage and usage.limit is not None and desired_value <= usage.limit:
            raise QuotaAlreadySufficient(f"{usage.label} is already {usage.limit:g} {usage.unit}.")
        existing = next(
            (
                r
                for r in self._store.list_requests(aws_account_id)
                if r.region == region and r.quotaCode == quota_code and r.is_open and r.desiredValue >= desired_value
            ),
            None,
        )
        if existing:
            return existing
        response = self._reader.request_increase(aws_account_id, region, quota.serviceCode, quota_code, desired_value)
        request = spoke_capacity.QuotaIncreaseRequest(
            awsAccountId=aws_account_id,
            region=region,
            serviceCode=quota.serviceCode,
            quotaCode=quota_code,
            desiredValue=float(response.get("DesiredValue", desired_value)),
            requestedBy=requested_by,
            requestedAt=datetime.now(timezone.utc).isoformat(),
            requestId=response.get("Id"),
            caseId=response.get("CaseId"),
            status=response.get("Status", spoke_capacity.QuotaRequestStatus.Pending.value),
        )
        self._store.put_request(request)
        return request

    def requests(self, aws_account_id: Optional[str] = None) -> list[spoke_capacity.QuotaIncreaseRequest]:
        return self._store.list_requests(aws_account_id)


def _quota_view(q: spoke_capacity.QuotaUsage) -> dict:
    return {
        "quotaCode": q.quotaCode,
        "serviceCode": q.serviceCode,
        "label": q.label,
        "unit": q.unit,
        "limit": q.limit,
        "used": q.used,
        "remaining": q.remaining,
        "usedPercent": q.used_percent,
    }


def _account_view(s: spoke_capacity.SpokeCapacity, requests: list[spoke_capacity.QuotaIncreaseRequest]) -> dict:
    return {
        "awsAccountId": s.awsAccountId,
        "region": s.region,
        "programs": [p.model_dump() for p in s.programs],
        "collectedAt": s.collectedAt,
        "error": s.error,
        "quotas": [_quota_view(q) for q in s.quotas],
        "instances": [i.model_dump() for i in s.instances],
        "gp3GiB": s.gp3GiB,
        "requests": [r.model_dump() for r in requests],
    }


def _totals(snapshots: list[spoke_capacity.SpokeCapacity]) -> dict:
    by_state: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    gpu_running = 0
    for s in snapshots:
        for i in s.instances:
            by_state[i.state] += i.workbenches
            if i.state in spoke_capacity.COUNTED_STATES:
                by_type[i.instanceType] += i.count
                if spoke_capacity.instance_family(i.instanceType) in ("g", "vt", "p"):
                    gpu_running += i.count
    return {
        "accounts": len(snapshots),
        "programs": len({p.projectId for s in snapshots for p in s.programs}),
        "workbenchesByState": dict(by_state),
        "runningByInstanceType": dict(sorted(by_type.items())),
        "gpuInstancesRunning": gpu_running,
        "gp3GiB": sum(s.gp3GiB for s in snapshots),
    }
