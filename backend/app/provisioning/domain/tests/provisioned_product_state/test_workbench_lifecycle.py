"""Workbench stop rules: layering, the admins' switches, the nightly stop, the
vew:autostop tag, and the user's choices."""

import logging
from unittest import mock

import pytest

from app.provisioning.domain.command_handlers.provisioned_product_state import workbench_lifecycle as service
from app.provisioning.domain.events.provisioned_product_state import provisioned_product_stop_initiated
from app.provisioning.domain.model import product_status, provisioned_product, workbench_lifecycle
from app.provisioning.domain.read_models import project

# A deployment that opts in to the nightly stop (the default is off).
PLATFORM = workbench_lifecycle.PlatformLifecycleDefaults(nightlyStop=True, timezone="Europe/Stockholm")
ALLOWING = workbench_lifecycle.ProgramLifecycle(
    allowUserDisableNightlyStop=True,
    allowUserIdleTimeout=True,
    userIdleTimeoutMinMinutes=10,
    userIdleTimeoutMaxMinutes=240,
)


# Layering ---------------------------------------------------------------------------------------------


def test_platform_defaults_apply_without_program_settings():
    effective = workbench_lifecycle.effective(PLATFORM, None, None)

    assert (effective.idleStopEnabled, effective.idleStopMinutes, effective.nightlyStop, effective.weekendStop) == (
        True,
        60,
        True,
        True,
    )
    assert effective.autostop_tag == "60"
    assert set(effective.sources.values()) == {"platform"}


def test_program_overrides_the_platform():
    program = workbench_lifecycle.ProgramLifecycle(idleStopMinutes=120, nightlyStop=False, weekendStop=False)

    effective = workbench_lifecycle.effective(PLATFORM, program, None)

    assert (effective.idleStopMinutes, effective.nightlyStop, effective.weekendStop) == (120, False, False)
    assert effective.sources["idleStopMinutes"] == effective.sources["nightlyStop"] == "program"


def test_user_choices_apply_within_the_programs_bounds():
    user = workbench_lifecycle.UserLifecycleSettings(nightlyStopDisabled=True, idleTimeoutMinutes=10)

    effective = workbench_lifecycle.effective(PLATFORM, ALLOWING, user)

    assert (effective.idleStopMinutes, effective.nightlyStop) == (10, False)
    assert effective.sources["idleStopMinutes"] == effective.sources["nightlyStop"] == "user"
    assert effective.ignoredUserSettings == []


def test_user_choices_are_ignored_once_the_program_revokes_them():
    user = workbench_lifecycle.UserLifecycleSettings(nightlyStopDisabled=True, idleTimeoutMinutes=300)

    effective = workbench_lifecycle.effective(PLATFORM, ALLOWING, user)  # 300 is above the program's 240
    revoked = workbench_lifecycle.effective(PLATFORM, workbench_lifecycle.ProgramLifecycle(), user)

    assert effective.idleStopMinutes == 60 and effective.ignoredUserSettings == ["idleTimeoutMinutes"]
    assert revoked.nightlyStop is True
    assert set(revoked.ignoredUserSettings) == {"idleTimeoutMinutes", "nightlyStopDisabled"}


def test_always_on_bypasses_every_stop():
    effective = workbench_lifecycle.effective(PLATFORM, workbench_lifecycle.ProgramLifecycle(alwaysOn=True), None)

    assert (effective.idleStopEnabled, effective.nightlyStop, effective.weekendStop) == (False, False, False)
    assert effective.autostop_tag == "off"
    assert (
        workbench_lifecycle.permissions(
            workbench_lifecycle.ProgramLifecycle(alwaysOn=True, allowUserIdleTimeout=True)
        ).maySetIdleTimeout
        is False
    )


@pytest.mark.parametrize(
    "program,user,message",
    [
        (None, workbench_lifecycle.UserLifecycleSettings(nightlyStopDisabled=True), "nightly"),
        (None, workbench_lifecycle.UserLifecycleSettings(idleTimeoutMinutes=90), "inactivity"),
        (ALLOWING, workbench_lifecycle.UserLifecycleSettings(idleTimeoutMinutes=5), "between 10 and 240"),
        (ALLOWING, workbench_lifecycle.UserLifecycleSettings(idleTimeoutMinutes=241), "between 10 and 240"),
    ],
)
def test_users_cannot_exceed_what_the_program_allows(program, user, message):
    with pytest.raises(workbench_lifecycle.LifecycleSettingsError, match=message):
        workbench_lifecycle.validate_user_settings(program, user)


# Service --------------------------------------------------------------------------------------------


def _pp(pp_id="pp-1", project_id="proj-1", status=product_status.ProductStatus.Running, **fields):
    return provisioned_product.ProvisionedProduct(
        projectId=project_id,
        provisionedProductId=pp_id,
        provisionedProductName="wb",
        provisionedProductType=provisioned_product.ProvisionedProductType.Workbench,
        userId="USER-1",
        userDomains=[],
        status=status,
        productId="prod-1",
        productName="Workbench",
        technologyId="tech-1",
        versionId="vers-1",
        versionName="1.0.0",
        awsAccountId="533813050837",
        accountId="acc-1",
        stage=provisioned_product.ProvisionedProductStage.DEV,
        region="eu-north-1",
        scProductId="sc-prod",
        scProvisioningArtifactId="pa-1",
        instanceId=f"i-{pp_id}",
        createDate="2026-09-30T00:00:00+00:00",
        lastUpdateDate="2026-09-30T00:00:00+00:00",
        createdBy="USER-1",
        lastUpdatedBy="USER-1",
        **fields,
    )


def _service(pps, programs=None):
    pp_qry = mock.Mock()
    pp_qry.get_all_provisioned_products.return_value = pps
    projects = mock.Mock()
    projects.get_projects.return_value = [
        project.Project(projectId=pid, workbenchLifecycle=settings) for pid, settings in (programs or {}).items()
    ]
    instances = mock.Mock()
    uow = mock.MagicMock()
    repo = uow.get_repository.return_value
    repo.get.side_effect = lambda pk: next(p for p in pps if p.provisionedProductId == pk.provisionedProductId)
    publisher = mock.Mock()
    srv = service.WorkbenchLifecycleService(
        platform=PLATFORM,
        pp_qry_srv=pp_qry,
        projects_qry_srv=projects,
        instance_mgmt_srv=instances,
        uow=uow,
        publisher=publisher,
        logger=logging.getLogger("test"),
    )
    return srv, instances, repo, publisher


def test_nightly_stop_stops_through_vews_own_stop_path():
    kept = _pp("pp-kept", project_id="proj-always")
    user_off = _pp(
        "pp-user-off",
        project_id="proj-allow",
        lifecycleSettings=workbench_lifecycle.UserLifecycleSettings(nightlyStopDisabled=True),
    )
    stopped = _pp("pp-stop")
    srv, _, _, publisher = _service(
        [kept, user_off, stopped],
        {"proj-always": {"alwaysOn": True}, "proj-allow": {"allowUserDisableNightlyStop": True}},
    )

    result = srv.nightly_stop()

    assert result["stopped"] == ["pp-stop"]
    assert set(result["kept"]) == {"pp-kept", "pp-user-off"}
    (agg,) = [call.args[0] for call in publisher.publish.call_args_list]
    events = [event for event, _ in agg.pending_events]
    assert [type(e) for e in events] == [provisioned_product_stop_initiated.ProvisionedProductStopInitiated]
    assert events[0].provisioned_product_id == "pp-stop"


def test_nightly_stop_dry_run_stops_nothing():
    srv, _, _, publisher = _service([_pp()])

    assert srv.nightly_stop(dry_run=True)["stopped"] == ["pp-1"]
    publisher.publish.assert_not_called()


def test_nightly_stop_skips_other_product_types():
    target = _pp("pp-vt").model_copy(
        update={"provisionedProductType": provisioned_product.ProvisionedProductType.VirtualTarget}
    )
    srv, _, _, publisher = _service([target])

    assert srv.nightly_stop()["stopped"] == []
    publisher.publish.assert_not_called()


def test_reconcile_writes_the_tag_only_on_changes():
    current = _pp("pp-same", appliedAutostopTag="60")
    changed = _pp("pp-new")
    srv, instances, repo, _ = _service([current, changed])

    result = srv.reconcile_tags()

    assert result["changed"] == [{"pp-new": "60"}]
    instances.set_instance_tags.assert_called_once_with(
        user_id=service.LIFECYCLE_PROCESS_NAME,
        aws_account_id="533813050837",
        region="eu-north-1",
        instance_id="i-pp-new",
        tags={"vew:autostop": "60"},
    )
    assert repo.update_entity.call_args.kwargs["entity"].appliedAutostopTag == "60"


def test_reconcile_carries_on_after_a_failure():
    srv, instances, _, _ = _service([_pp("pp-a"), _pp("pp-b")])
    instances.set_instance_tags.side_effect = [RuntimeError("AccessDenied"), None]

    result = srv.reconcile_tags()

    assert result["failed"] == ["pp-a"]
    assert len(result["changed"]) == 2


def test_user_settings_are_validated_stored_and_applied():
    pp = _pp()
    srv, instances, repo, _ = _service([pp], {"proj-1": ALLOWING.model_dump()})

    _, effective = srv.update_user_settings(pp, workbench_lifecycle.UserLifecycleSettings(idleTimeoutMinutes=10))

    assert effective.autostop_tag == "10"
    instances.set_instance_tags.assert_called_once()
    stored = repo.update_entity.call_args.kwargs["entity"]
    assert (stored.lifecycleSettings.idleTimeoutMinutes, stored.appliedAutostopTag) == (10, "10")


def test_user_settings_beyond_the_programs_bounds_are_refused():
    pp = _pp()
    srv, instances, repo, _ = _service([pp], {"proj-1": ALLOWING.model_dump()})

    with pytest.raises(workbench_lifecycle.LifecycleSettingsError):
        srv.update_user_settings(pp, workbench_lifecycle.UserLifecycleSettings(idleTimeoutMinutes=600))

    instances.set_instance_tags.assert_not_called()
    repo.update_entity.assert_not_called()


def test_stopped_workbenches_get_the_tag_when_they_run_again():
    pp = _pp(status=product_status.ProductStatus.Stopped)
    srv, instances, repo, _ = _service([pp], {"proj-1": ALLOWING.model_dump()})

    srv.update_user_settings(pp, workbench_lifecycle.UserLifecycleSettings(idleTimeoutMinutes=30))

    instances.set_instance_tags.assert_not_called()
    assert repo.update_entity.call_args.kwargs["entity"].lifecycleSettings.idleTimeoutMinutes == 30
