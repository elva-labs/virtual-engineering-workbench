"""The hub-side idle decision: signals in, a stop through VEW's own path out; missing data
never stops a workbench."""

import logging
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from app.provisioning.domain.command_handlers.provisioned_product_state import workbench_lifecycle as service
from app.provisioning.domain.model import idle_signals, product_status, provisioned_product, workbench_lifecycle
from app.provisioning.domain.read_models import project

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
CONFIG = idle_signals.IdleStopConfig()


def _series(minutes: int, value: float = 0.0, end: datetime = NOW) -> list[tuple[datetime, float]]:
    """One 5-minute data point per period over the last `minutes`, oldest first."""
    return [(end - timedelta(minutes=m), value) for m in range(minutes - 5, -1, -5)]


def _quiet(minutes: int) -> dict:
    return {signal: _series(minutes) for signal in CONFIG.signals}


def _decide(signals, idle=60, started=NOW - timedelta(hours=5), config=CONFIG):
    return idle_signals.decide(signals=signals, idle_minutes=idle, now=NOW, started_at=started, config=config)


# The decision -------------------------------------------------------------------------------------


def test_quiet_for_the_whole_timeout_stops():
    decision = _decide(_quiet(120))

    assert decision.action == "stop"
    assert decision.reason == "Stopped after 60 minutes without activity"


@pytest.mark.parametrize(
    "signal,value",
    [("dcvConnections", 1), ("interactiveSessions", 2), ("load", 0.15)],
)
def test_any_busy_signal_in_the_window_keeps_it(signal, value):
    signals = _quiet(120)
    signals[signal][-3] = (signals[signal][-3][0], value)

    decision = _decide(signals)

    assert decision.action == "keep" and signal in decision.reason


def test_busy_before_the_window_does_not_count():
    signals = _quiet(120)
    signals["dcvConnections"][0] = (signals["dcvConnections"][0][0], 1)  # 2 hours ago

    assert _decide(signals).action == "stop"


def test_a_signal_switched_off_in_config_is_ignored():
    signals = _quiet(120)
    signals["load"] = [(ts, 0.9) for ts, _ in signals["load"]]
    config = idle_signals.IdleStopConfig(signals=["dcvConnections", "interactiveSessions"])

    assert _decide({k: v for k, v in signals.items() if k in config.signals}, config=config).action == "stop"


def test_gaps_in_the_window_keep_it():
    signals = _quiet(120)
    signals["load"] = signals["load"][:-6]  # the last 30 minutes missing

    decision = _decide(signals)

    assert decision.action == "keep" and "not enough data" in decision.reason


def test_no_data_at_all_is_reported_never_stopped():
    assert _decide({signal: [] for signal in CONFIG.signals}).action == "missing"


def test_no_data_shortly_after_start_is_not_yet_missing():
    decision = _decide({signal: [] for signal in CONFIG.signals}, started=NOW - timedelta(minutes=30))

    assert decision.action == "keep"


def test_recently_started_workbenches_are_kept():
    assert _decide(_quiet(120), started=NOW - timedelta(minutes=10)).action == "keep"
    assert _decide(_quiet(120), started=NOW - timedelta(minutes=40)).reason == (
        "running for less than the inactivity timeout"
    )


# The job ------------------------------------------------------------------------------------------


def _pp(pp_id="pp-1", project_id="proj-1", **fields):
    defaults = dict(
        projectId=project_id,
        provisionedProductId=pp_id,
        provisionedProductName="wb",
        provisionedProductType=provisioned_product.ProvisionedProductType.Workbench,
        userId="USER-1",
        userDomains=[],
        status=product_status.ProductStatus.Running,
        productId="prod-1",
        productName="Workbench",
        technologyId="tech-1",
        versionId="vers-1",
        versionName="1.0.0",
        awsAccountId="111111111111",
        accountId="acc-1",
        stage=provisioned_product.ProvisionedProductStage.DEV,
        region="eu-north-1",
        scProductId="sc-prod",
        scProvisioningArtifactId="pa-1",
        instanceId=f"i-{pp_id}",
        createDate="2026-10-01T00:00:00+00:00",
        lastUpdateDate="2026-10-01T00:00:00+00:00",
        startDate="2026-10-01T06:00:00+00:00",
        createdBy="USER-1",
        lastUpdatedBy="USER-1",
    )
    defaults.update(fields)
    return provisioned_product.ProvisionedProduct(**defaults)


def _service(pps, signals_by_instance, programs=None, config=CONFIG):
    pp_qry = mock.Mock()
    pp_qry.get_all_provisioned_products.return_value = pps
    projects = mock.Mock()
    projects.get_projects.return_value = [
        project.Project(projectId=pid, workbenchLifecycle=settings) for pid, settings in (programs or {}).items()
    ]
    signals = mock.Mock()

    def _get(aws_account_id, region, instance_id, metric_names, start, end):
        value = signals_by_instance[instance_id]
        if isinstance(value, Exception):
            raise value
        names = {v: k for k, v in idle_signals.SIGNAL_METRICS.items()}
        return {name: value.get(names[name], []) for name in metric_names}

    signals.get_signals.side_effect = _get
    publisher = mock.Mock()
    srv = service.WorkbenchLifecycleService(
        platform=workbench_lifecycle.PlatformLifecycleDefaults(),
        pp_qry_srv=pp_qry,
        projects_qry_srv=projects,
        instance_mgmt_srv=mock.Mock(),
        uow=mock.MagicMock(),
        publisher=publisher,
        logger=logging.getLogger("test"),
        signals_srv=signals,
        idle_config=config,
        clock=lambda: NOW,
    )
    return srv, publisher


def test_idle_workbenches_stop_through_vews_stop_path_with_a_reason():
    idle, busy = _pp("pp-idle"), _pp("pp-busy")
    busy_signals = _quiet(120)
    busy_signals["dcvConnections"][-1] = (NOW, 1)
    srv, publisher = _service([idle, busy], {"i-pp-idle": _quiet(120), "i-pp-busy": busy_signals})

    result = srv.idle_stop()

    assert result["stopped"] == ["pp-idle"]
    assert result["kept"] == {"pp-busy": "busy (dcvConnections)"}
    (agg,) = [call.args[0] for call in publisher.publish.call_args_list]
    assert agg._provisioned_product.status == product_status.ProductStatus.Stopping
    assert agg._provisioned_product.lastUpdatedBy == service.IDLE_STOP_PROCESS_NAME
    assert agg._provisioned_product.statusReason == "Stopped after 60 minutes without activity"


def test_dry_run_from_config_stops_nothing():
    srv, publisher = _service(
        [_pp("pp-idle")], {"i-pp-idle": _quiet(120)}, config=idle_signals.IdleStopConfig(dryRun=True)
    )

    result = srv.idle_stop()

    assert result["dryRun"] is True and result["stopped"] == ["pp-idle"]
    publisher.publish.assert_not_called()


def test_the_programs_timeout_and_opt_outs_apply():
    longer = _pp("pp-longer", project_id="proj-long")
    always = _pp("pp-always", project_id="proj-always")
    protected = _pp(
        "pp-protected",
        outputs=[
            {
                "outputKey": "FeatureToggles",
                "outputValue": '[{"feature": "AutoStopProtection", "enabled": true}]',
                "outputType": "String",
            }
        ],
    )
    srv, publisher = _service(
        [longer, always, protected],
        {"i-pp-longer": _quiet(120), "i-pp-always": _quiet(120), "i-pp-protected": _quiet(120)},
        programs={"proj-long": {"idleStopMinutes": 240}, "proj-always": {"alwaysOn": True}},
    )

    result = srv.idle_stop()

    assert result["stopped"] == []
    assert result["kept"]["pp-always"] == "idle stop off"
    assert result["kept"]["pp-longer"].startswith("not enough data")
    publisher.publish.assert_not_called()


def test_missing_signals_and_read_failures_never_stop():
    srv, publisher = _service(
        [_pp("pp-silent"), _pp("pp-broken")],
        {"i-pp-silent": {}, "i-pp-broken": RuntimeError("AccessDenied")},
    )

    result = srv.idle_stop()

    assert result["missing"] == ["pp-silent"] and result["failed"] == ["pp-broken"]
    publisher.publish.assert_not_called()


def test_disabled_does_nothing():
    srv, publisher = _service([_pp()], {"i-pp-1": _quiet(120)}, config=idle_signals.IdleStopConfig(enabled=False))

    assert srv.idle_stop()["disabled"] is True
    publisher.publish.assert_not_called()
