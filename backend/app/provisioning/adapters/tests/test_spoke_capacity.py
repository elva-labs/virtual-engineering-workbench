"""Spoke capacity - the launch check, the collector, the views and quota requests."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.provisioning.adapters.services import aws_spoke_capacity_service
from app.provisioning.domain.model import spoke_capacity
from app.provisioning.domain.query_services import spoke_capacity_domain_query_service as svc

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

CONFIG = spoke_capacity.CapacityConfig(
    enabled=True,
    headroomVcpus=0,
    staleAfterMinutes=30,
    quotas=[
        spoke_capacity.QuotaConfig(
            serviceCode="ec2",
            quotaCode="L-1216C47A",
            label="Standard vCPU",
            instanceFamilies=["a", "c", "d", "h", "i", "m", "r", "t", "z"],
        ),
        spoke_capacity.QuotaConfig(
            serviceCode="ec2", quotaCode="L-DB2E81BA", label="GPU vCPU (G, VT)", instanceFamilies=["g", "vt"]
        ),
        spoke_capacity.QuotaConfig(
            serviceCode="ebs", quotaCode="L-7A658B76", label="gp3 storage (TiB)", volumeType="gp3"
        ),
    ],
)


def snapshot(standard=(60, 52), gpu=(0, 0), gp3=(50, 1.0), age_minutes=5, programs=None):
    return spoke_capacity.SpokeCapacity(
        awsAccountId="123456789012",
        region="eu-north-1",
        collectedAt=(NOW - timedelta(minutes=age_minutes)).isoformat(),
        programs=programs or [spoke_capacity.ProgramRef(projectId="proj-a", projectName="AiPlatform", stages=["DEV"])],
        quotas=[
            spoke_capacity.QuotaUsage(
                serviceCode="ec2",
                quotaCode="L-1216C47A",
                label="Standard vCPU",
                unit="vCPU",
                limit=standard[0],
                used=standard[1],
            ),
            spoke_capacity.QuotaUsage(
                serviceCode="ec2",
                quotaCode="L-DB2E81BA",
                label="GPU vCPU (G, VT)",
                unit="vCPU",
                limit=gpu[0],
                used=gpu[1],
            ),
            spoke_capacity.QuotaUsage(
                serviceCode="ebs",
                quotaCode="L-7A658B76",
                label="gp3 storage (TiB)",
                unit="TiB",
                limit=gp3[0],
                used=gp3[1],
            ),
        ],
    )


# --- model -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instance_type,family,vcpus",
    [
        ("m7i.xlarge", "m", 4),
        ("m7i.2xlarge", "m", 8),
        ("m7i.4xlarge", "m", 16),
        ("g6.xlarge", "g", 4),
        ("g6.4xlarge", "g", 16),
        ("vt1.3xlarge", "vt", 12),
        ("t3.medium", "t", 2),
        ("m7i.metal-24xl", "m", None),
    ],
)
def test_family_and_vcpus(instance_type, family, vcpus):
    assert spoke_capacity.instance_family(instance_type) == family
    assert spoke_capacity.vcpus_of(instance_type) == vcpus


def test_reported_vcpus_win_over_the_size_rule():
    assert spoke_capacity.vcpus_of("t3.large", {"t3.large": 2}) == 2
    assert spoke_capacity.vcpus_of("weird.size", {"weird.size": 6}) == 6


def test_a_standard_launch_that_fits():
    assert spoke_capacity.check_launch(CONFIG, snapshot(), "m7i.xlarge", 250, NOW).fits


def test_a_standard_launch_that_does_not_fit():
    result = spoke_capacity.check_launch(CONFIG, snapshot(standard=(60, 52)), "m7i.4xlarge", 250, NOW)
    assert not result.fits
    assert result.quotaCode == "L-1216C47A" and result.required == 16 and result.remaining == 8
    assert "8 of 60 vCPU" in result.reason


def test_gpu_with_a_zero_quota_is_refused_as_gpu_capacity():
    result = spoke_capacity.check_launch(CONFIG, snapshot(gpu=(0, 0)), "g6.xlarge", 250, NOW)
    assert not result.fits
    assert result.reason.startswith("GPU capacity")


def test_headroom_is_kept_free():
    config = CONFIG.model_copy(update={"headroomVcpus": 8})
    assert not spoke_capacity.check_launch(config, snapshot(standard=(60, 48)), "m7i.2xlarge", None, NOW).fits
    assert spoke_capacity.check_launch(config, snapshot(standard=(60, 48)), "m7i.xlarge", None, NOW).fits


def test_storage_that_does_not_fit():
    result = spoke_capacity.check_launch(CONFIG, snapshot(gp3=(1, 0.9)), "m7i.xlarge", 250, NOW)
    assert not result.fits and result.quotaCode == "L-7A658B76"


@pytest.mark.parametrize(
    "snap,instance_type",
    [
        (None, "g6.xlarge"),  # no data yet
        (snapshot(gpu=(0, 0), age_minutes=31), "g6.xlarge"),  # stale
        (snapshot(gpu=(None, 0)), "g6.xlarge"),  # quota unreadable
        (snapshot(), "x2iedn.metal"),  # no quota watched for the family
    ],
)
def test_the_check_fails_open(snap, instance_type):
    assert spoke_capacity.check_launch(CONFIG, snap, instance_type, 250, NOW).fits


def test_disabled_never_refuses():
    config = CONFIG.model_copy(update={"enabled": False})
    assert spoke_capacity.check_launch(config, snapshot(gpu=(0, 0)), "g6.xlarge", None, NOW).fits


def test_available_per_size_for_the_launch_form():
    available = spoke_capacity.available_per_size(
        CONFIG, snapshot(standard=(60, 36)), ["m7i.xlarge", "m7i.2xlarge", "m7i.4xlarge", "g6.xlarge", "x2iedn.metal"]
    )
    assert available == {"m7i.xlarge": 6, "m7i.2xlarge": 3, "m7i.4xlarge": 1, "g6.xlarge": 0, "x2iedn.metal": None}


def test_vcpu_usage_counts_running_and_pending_only():
    instances = [
        spoke_capacity.InstanceCount(instanceType="m7i.xlarge", state="running", count=2, vcpus=4),
        spoke_capacity.InstanceCount(instanceType="m7i.2xlarge", state="pending", count=1, vcpus=8),
        spoke_capacity.InstanceCount(instanceType="m7i.4xlarge", state="stopped", count=3, vcpus=16),
        spoke_capacity.InstanceCount(instanceType="g6.xlarge", state="running", count=1, vcpus=4),
    ]
    assert spoke_capacity.vcpu_usage(CONFIG, instances) == {"L-1216C47A": 16, "L-DB2E81BA": 4}


def test_used_percent_of_a_zero_quota():
    usage = spoke_capacity.QuotaUsage(serviceCode="ec2", quotaCode="q", label="l", unit="vCPU", limit=0, used=0)
    assert usage.used_percent == 0.0
    assert usage.model_copy(update={"used": 4}).used_percent == 100.0


# --- the AWS reader --------------------------------------------------------------------------------


class _Paginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_):
        return iter(self._pages)


class FakeEc2:
    def __init__(self):
        self.instances = [
            {
                "InstanceType": "m7i.xlarge",
                "State": {"Name": "running"},
                "Tags": [{"Key": "vew:provisionedProduct:productType", "Value": "WORKBENCH"}],
            },
            {
                "InstanceType": "m7i.xlarge",
                "State": {"Name": "stopped"},
                "Tags": [{"Key": "vew:provisionedProduct:productType", "Value": "WORKBENCH"}],
            },
            {"InstanceType": "t3.medium", "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "jumphost"}]},
            {"InstanceType": "m7i.4xlarge", "State": {"Name": "running"}, "InstanceLifecycle": "spot"},
        ]

    def get_paginator(self, name):
        if name == "describe_instances":
            return _Paginator([{"Reservations": [{"Instances": self.instances}]}])
        if name == "describe_instance_types":
            return _Paginator(
                [
                    {
                        "InstanceTypes": [
                            {"InstanceType": "m7i.xlarge", "VCpuInfo": {"DefaultVCpus": 4}},
                            {"InstanceType": "t3.medium", "VCpuInfo": {"DefaultVCpus": 2}},
                        ]
                    }
                ]
            )
        if name == "describe_volumes":
            return _Paginator([{"Volumes": [{"Size": 250}, {"Size": 774}]}])
        raise AssertionError(name)


class _AlreadyExists(Exception):
    pass


class FakeQuotas:
    exceptions = SimpleNamespace(ResourceAlreadyExistsException=_AlreadyExists)

    def __init__(self, values, open_request=None):
        self.values = values
        self.calls = 0
        self.open_request = open_request
        self.requests = []

    def get_service_quota(self, ServiceCode, QuotaCode):
        self.calls += 1
        if QuotaCode not in self.values:
            raise RuntimeError("AccessDenied")
        return {"Quota": {"Value": self.values[QuotaCode]}}

    def request_service_quota_increase(self, ServiceCode, QuotaCode, DesiredValue):
        if self.open_request:
            raise _AlreadyExists()
        self.requests.append((ServiceCode, QuotaCode, DesiredValue))
        return {"RequestedQuota": {"Id": "req-1", "Status": "PENDING", "DesiredValue": DesiredValue}}

    def list_requested_service_quota_change_history_by_quota(self, ServiceCode, QuotaCode):
        return {"RequestedQuotas": [self.open_request]}

    def get_requested_service_quota_change(self, RequestId):
        return {"RequestedQuota": {"Id": RequestId, "Status": "CASE_OPENED", "CaseId": "case-9"}}


def reader(quotas):
    return aws_spoke_capacity_service.AWSSpokeCapacityService(
        servicequotas_client_provider=lambda *_: quotas,
        ec2_client_provider=lambda *_: FakeEc2(),
        logger=logging.getLogger("test"),
    )


def test_the_reader_counts_what_uses_each_quota():
    quotas = FakeQuotas({"L-1216C47A": 60.0, "L-DB2E81BA": 0.0, "L-7A658B76": 50.0})
    snap = reader(quotas).read(CONFIG, "123456789012", "eu-north-1")
    assert snap.error is None
    assert snap.quota("L-1216C47A").used == 6  # the running workbench (4) and the jumphost (2); spot excluded
    assert snap.quota("L-DB2E81BA").limit == 0 and snap.quota("L-DB2E81BA").used == 0
    assert snap.gp3GiB == 1024 and snap.quota("L-7A658B76").used == 1.0
    running = next(i for i in snap.instances if i.instanceType == "m7i.xlarge" and i.state == "running")
    assert running.workbenches == 1 and running.vcpus == 4


def test_quota_values_are_cached_and_an_unreadable_one_is_reported():
    quotas = FakeQuotas({"L-1216C47A": 60.0, "L-7A658B76": 50.0})
    r = reader(quotas)
    snap = r.read(CONFIG, "a", "eu-north-1")
    assert snap.quota("L-DB2E81BA").limit is None and "L-DB2E81BA" in snap.error
    calls = quotas.calls
    r.read(CONFIG, "a", "eu-north-1")
    assert quotas.calls == calls + 1  # only the unreadable one is asked again


# --- the domain service ----------------------------------------------------------------------------


class MemoryStore:
    def __init__(self):
        self.snapshots, self.requests = {}, []

    def put_snapshot(self, s):
        self.snapshots[(s.awsAccountId, s.region)] = s

    def get_snapshot(self, account, region):
        return self.snapshots.get((account, region))

    def list_snapshots(self):
        return list(self.snapshots.values())

    def put_request(self, r):
        self.requests = [x for x in self.requests if x.requestedAt != r.requestedAt] + [r]

    def list_requests(self, account=None):
        return [r for r in self.requests if account is None or r.awsAccountId == account]


class FakeProjects:
    def get_projects(self):
        return [
            SimpleNamespace(projectId="proj-a", projectName="AiPlatform"),
            SimpleNamespace(projectId="proj-b", projectName="Empty"),
        ]

    def get_aws_accounts_by_status(self, project_id, statuses):
        if project_id == "proj-b":
            raise RuntimeError("no accounts")
        return [
            SimpleNamespace(awsAccountId="123456789012", region="eu-north-1", stage="DEV"),
            SimpleNamespace(awsAccountId="123456789012", region="eu-north-1", stage="PROD"),
        ]


def service(quotas=None, store=None):
    quotas = quotas or FakeQuotas({"L-1216C47A": 60.0, "L-DB2E81BA": 0.0, "L-7A658B76": 50.0})
    return svc.SpokeCapacityDomainQueryService(
        config=CONFIG,
        store=store or MemoryStore(),
        reader=reader(quotas),
        projects_qry_srv=FakeProjects(),
        logger=logging.getLogger("test"),
        default_region="eu-north-1",
    )


def test_collect_reads_each_spoke_once_with_its_programs_and_stages():
    s = service()
    snapshots = s.collect()
    assert len(snapshots) == 1
    assert snapshots[0].programs[0].stages == ["DEV", "PROD"]
    overview = s.overview()
    assert overview["totals"]["accounts"] == 1 and overview["totals"]["workbenchesByState"] == {
        "running": 1,
        "stopped": 1,
    }
    assert overview["totals"]["runningByInstanceType"] == {"m7i.xlarge": 1, "t3.medium": 1}


def test_check_launch_raises_when_it_does_not_fit():
    store = MemoryStore()
    store.put_snapshot(snapshot(gpu=(0, 0)).model_copy(update={"collectedAt": datetime.now(timezone.utc).isoformat()}))
    s = service(store=store)
    with pytest.raises(spoke_capacity.CapacityExceeded):
        s.check_launch("123456789012", "eu-north-1", "g6.xlarge", 250)
    assert s.check_launch("123456789012", "eu-north-1", "m7i.xlarge", 250).fits


def test_request_increase_is_idempotent_and_refuses_a_lower_value():
    quotas = FakeQuotas({"L-1216C47A": 60.0, "L-DB2E81BA": 0.0, "L-7A658B76": 50.0})
    store = MemoryStore()
    s = service(quotas, store)
    s.collect()
    first = s.request_increase("123456789012", "eu-north-1", "L-DB2E81BA", 16, "admin@example.com")
    again = s.request_increase("123456789012", "eu-north-1", "L-DB2E81BA", 8, "admin@example.com")
    assert first.requestId == "req-1" and again.requestId == "req-1" and len(quotas.requests) == 1
    with pytest.raises(svc.QuotaAlreadySufficient):
        s.request_increase("123456789012", "eu-north-1", "L-1216C47A", 32, "admin@example.com")
    with pytest.raises(svc.UnknownQuota):
        s.request_increase("123456789012", "eu-north-1", "L-00000000", 1, "admin@example.com")


def test_an_open_request_at_aws_is_adopted():
    quotas = FakeQuotas(
        {"L-1216C47A": 60.0, "L-DB2E81BA": 0.0, "L-7A658B76": 50.0},
        open_request={"Id": "req-7", "Status": "CASE_OPENED", "DesiredValue": 32.0, "CaseId": "c"},
    )
    s = service(quotas)
    s.collect()
    request = s.request_increase("123456789012", "eu-north-1", "L-DB2E81BA", 16, "admin@example.com")
    assert request.requestId == "req-7" and request.desiredValue == 32.0


def test_collect_refreshes_open_requests():
    store = MemoryStore()
    s = service(store=store)
    s.collect()
    s.request_increase("123456789012", "eu-north-1", "L-DB2E81BA", 16, "admin@example.com")
    s.collect()
    assert store.requests[0].status == "CASE_OPENED" and store.requests[0].caseId == "case-9"
