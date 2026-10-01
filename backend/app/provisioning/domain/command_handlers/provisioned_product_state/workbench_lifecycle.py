"""Workbench stop rules in action.

- The nightly stop (EventBridge Scheduler, in the deployment's time zone) stops running workbenches whose effective
  setting keeps the nightly stop on. It goes through VEW's own stop path, so status, events and records
  stay consistent. Nothing here ever starts a workbench.
- The reconcile run writes the effective inactivity timeout to the instance tag the idle agent reads
  (vew:autostop), so program and user changes reach running workbenches within minutes.
- The user API reads and changes the owner's choices, only within what the program allows.
- The idle stop: the scheduled job reads each running workbench's signals from its account's
  CloudWatch and stops it through the same stop path once it stayed quiet for its inactivity timeout.
  The workbench agent only reports; missing data never stops anything.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.provisioning.domain.aggregates import provisioned_product_state_aggregate
from app.provisioning.domain.commands.provisioned_product_state import initiate_provisioned_product_stop_command
from app.provisioning.domain.model import idle_signals, product_status, provisioned_product, workbench_lifecycle
from app.provisioning.domain.ports import (
    instance_management_service,
    projects_query_service,
    provisioned_products_query_service,
    workbench_signals_service,
)
from app.provisioning.domain.value_objects import (
    project_id_value_object,
    provisioned_product_id_value_object,
    user_id_value_object,
)
from app.shared.adapters.feature_toggling import product_feature_toggles
from app.shared.adapters.unit_of_work_v2 import unit_of_work
from app.shared.ddd import aggregate

NIGHTLY_STOP_PROCESS_NAME = "VEWProvisioningBCNightlyStop"
LIFECYCLE_PROCESS_NAME = "VEWProvisioningBCLifecycle"
IDLE_STOP_PROCESS_NAME = "VEWProvisioningBCIdleStop"


class WorkbenchLifecycleService:
    def __init__(
        self,
        platform: workbench_lifecycle.PlatformLifecycleDefaults,
        pp_qry_srv: provisioned_products_query_service.ProvisionedProductsQueryService,
        projects_qry_srv: projects_query_service.ProjectsQueryService,
        instance_mgmt_srv: instance_management_service.InstanceManagementService,
        uow: unit_of_work.UnitOfWork,
        publisher: aggregate.AggregatePublisher | None,
        logger: logging.Logger,
        signals_srv: workbench_signals_service.WorkbenchSignalsService | None = None,
        idle_config: idle_signals.IdleStopConfig | None = None,
        clock=lambda: datetime.now(timezone.utc),
    ):
        self._platform = platform
        self._pp_qry_srv = pp_qry_srv
        self._projects_qry_srv = projects_qry_srv
        self._instance_mgmt_srv = instance_mgmt_srv
        self._uow = uow
        self._publisher = publisher
        self._logger = logger
        self._signals_srv = signals_srv
        self._idle_config = idle_config or idle_signals.IdleStopConfig()
        self._clock = clock

    @property
    def platform_defaults(self) -> workbench_lifecycle.PlatformLifecycleDefaults:
        return self._platform

    # Settings -------------------------------------------------------------------------------------

    def programs(self) -> dict[str, workbench_lifecycle.ProgramLifecycle | None]:
        return {
            p.projectId: (
                workbench_lifecycle.ProgramLifecycle.model_validate(p.workbenchLifecycle)
                if p.workbenchLifecycle
                else None
            )
            for p in self._projects_qry_srv.get_projects()
        }

    def program(self, project_id: str) -> workbench_lifecycle.ProgramLifecycle | None:
        return self.programs().get(project_id)

    def effective_for(
        self,
        pp: provisioned_product.ProvisionedProduct,
        program: workbench_lifecycle.ProgramLifecycle | None,
    ) -> workbench_lifecycle.EffectiveLifecycle:
        return workbench_lifecycle.effective(self._platform, program, pp.lifecycleSettings)

    def update_user_settings(
        self,
        pp: provisioned_product.ProvisionedProduct,
        settings: workbench_lifecycle.UserLifecycleSettings,
    ) -> tuple[workbench_lifecycle.ProgramLifecycle | None, workbench_lifecycle.EffectiveLifecycle]:
        """The owner's choice; refused (LifecycleSettingsError) when the program does not allow it."""
        program = self.program(pp.projectId)
        workbench_lifecycle.validate_user_settings(program, settings)
        pp.lifecycleSettings = settings
        effective = self.effective_for(pp, program)
        # Straight away if it runs, so a longer timeout protects the next idle check already.
        if pp.status == product_status.ProductStatus.Running:
            self._apply_tag(pp, effective, dry_run=False)
        self._save(pp, lifecycleSettings=settings, appliedAutostopTag=pp.appliedAutostopTag)
        return program, effective

    # Jobs -----------------------------------------------------------------------------------------

    def _workbenches(self, status: product_status.ProductStatus) -> list[provisioned_product.ProvisionedProduct]:
        return [
            pp
            for pp in self._pp_qry_srv.get_all_provisioned_products(status=status)
            if pp.provisionedProductType == provisioned_product.ProvisionedProductType.Workbench
        ]

    def running_workbenches(self) -> list[provisioned_product.ProvisionedProduct]:
        return self._workbenches(product_status.ProductStatus.Running)

    def nightly_stop(self, dry_run: bool = False) -> dict:
        """Stops running workbenches that keep the nightly stop. Never starts anything."""
        programs = self.programs()
        stopped, kept = [], []
        for pp in self._workbenches(product_status.ProductStatus.Running):
            effective = self.effective_for(pp, programs.get(pp.projectId))
            toggles = product_feature_toggles.ProductFeatureToggles(outputs=pp.outputs)
            if not effective.nightlyStop or toggles.is_enabled(
                product_feature_toggles.ProductFeature.AutoStopProtection
            ):
                kept.append(pp.provisionedProductId)
                continue
            stopped.append(pp.provisionedProductId)
            if dry_run:
                continue
            self._stop(pp, NIGHTLY_STOP_PROCESS_NAME)
        self._logger.info({"job": "nightly-stop", "dryRun": dry_run, "stopped": stopped, "kept": kept})
        return {"dryRun": dry_run, "stopped": stopped, "kept": kept}

    def weekend_stop_allowed(self, pp: provisioned_product.ProvisionedProduct, programs: dict) -> bool:
        return self.effective_for(pp, programs.get(pp.projectId)).weekendStop

    def reconcile_tags(self, dry_run: bool = False) -> dict:
        """Writes vew:autostop where the effective value changed (running workbenches only)."""
        programs = self.programs()
        changed, failed = [], []
        for pp in self._workbenches(product_status.ProductStatus.Running):
            effective = self.effective_for(pp, programs.get(pp.projectId))
            if pp.appliedAutostopTag == effective.autostop_tag:
                continue
            changed.append({pp.provisionedProductId: effective.autostop_tag})
            if dry_run:
                continue
            try:
                self._apply_tag(pp, effective, dry_run=False)
                self._save(pp, appliedAutostopTag=pp.appliedAutostopTag)
            except Exception as error:  # one broken workbench must not stop the others
                failed.append(pp.provisionedProductId)
                self._logger.warning({"job": "lifecycle-reconcile", "pp": pp.provisionedProductId, "error": str(error)})
        self._logger.info({"job": "lifecycle-reconcile", "dryRun": dry_run, "changed": changed, "failed": failed})
        return {"dryRun": dry_run, "changed": changed, "failed": failed}

    def idle_stop(self, dry_run: bool = False) -> dict:
        """ADR 0026: stops running workbenches whose signals stayed quiet for their inactivity timeout.

        stopped: stopped now (or would be, in a dry run); missing: running without any signal for
        missingSignalAlarmMinutes - never stopped, the job counts them for an alarm; failed: the
        signals could not be read (also never stopped).
        """
        config = self._idle_config
        dry_run = dry_run or config.dryRun
        result = {"dryRun": dry_run, "stopped": [], "missing": [], "failed": [], "kept": {}}
        if not config.enabled or self._signals_srv is None:
            result["disabled"] = True
            return result
        programs = self.programs()
        now = self._clock()
        metric_names = [idle_signals.SIGNAL_METRICS[s] for s in config.signals]
        for pp in self._workbenches(product_status.ProductStatus.Running):
            pp_id = pp.provisionedProductId
            effective = self.effective_for(pp, programs.get(pp.projectId))
            toggles = product_feature_toggles.ProductFeatureToggles(outputs=pp.outputs)
            if not effective.idleStopEnabled or toggles.is_enabled(
                product_feature_toggles.ProductFeature.AutoStopProtection
            ):
                result["kept"][pp_id] = "idle stop off"
                continue
            if not pp.instanceId:
                result["kept"][pp_id] = "no instance"
                continue
            lookback = idle_signals.lookback_minutes(effective.idleStopMinutes, config)
            try:
                raw = self._signals_srv.get_signals(
                    aws_account_id=pp.awsAccountId,
                    region=pp.region,
                    instance_id=pp.instanceId,
                    metric_names=metric_names,
                    start=now - timedelta(minutes=lookback),
                    end=now,
                )
            except Exception as error:  # one unreadable spoke must not stop the others
                result["failed"].append(pp_id)
                self._logger.warning({"job": "idle-stop", "pp": pp_id, "error": str(error)})
                continue
            by_signal = {s: raw.get(idle_signals.SIGNAL_METRICS[s], []) for s in config.signals}
            decision = idle_signals.decide(
                signals=by_signal,
                idle_minutes=effective.idleStopMinutes,
                now=now,
                started_at=_parse_time(pp.startDate),
                config=config,
            )
            if decision.action == "missing":
                result["missing"].append(pp_id)
            elif decision.action == "keep":
                result["kept"][pp_id] = decision.reason
            else:
                result["stopped"].append(pp_id)
                if not dry_run:
                    self._stop(pp, IDLE_STOP_PROCESS_NAME, reason=decision.reason)
        self._logger.info({"job": "idle-stop", **result})
        return result

    # Internals ------------------------------------------------------------------------------------

    def _apply_tag(
        self,
        pp: provisioned_product.ProvisionedProduct,
        effective: workbench_lifecycle.EffectiveLifecycle,
        dry_run: bool,
    ) -> None:
        if not pp.instanceId or dry_run:
            return
        self._instance_mgmt_srv.set_instance_tags(
            user_id=LIFECYCLE_PROCESS_NAME,
            aws_account_id=pp.awsAccountId,
            region=pp.region,
            instance_id=pp.instanceId,
            tags={workbench_lifecycle.AUTOSTOP_TAG_KEY: effective.autostop_tag},
        )
        pp.appliedAutostopTag = effective.autostop_tag

    def _save(self, pp: provisioned_product.ProvisionedProduct, **fields) -> None:
        # Only the lifecycle fields: a concurrent status change on the same item must not be undone.
        pk = provisioned_product.ProvisionedProductPrimaryKey(
            projectId=pp.projectId, provisionedProductId=pp.provisionedProductId
        )
        with self._uow:
            repo = self._uow.get_repository(
                provisioned_product.ProvisionedProductPrimaryKey, provisioned_product.ProvisionedProduct
            )
            current = repo.get(pk)
            if current is None:
                return
            for key, value in fields.items():
                setattr(current, key, value)
            current.lastUpdateDate = datetime.now(timezone.utc).isoformat()
            repo.update_entity(pk=pk, entity=current)
            self._uow.commit()

    def _stop(self, pp: provisioned_product.ProvisionedProduct, process_name: str, reason: str | None = None) -> None:
        if self._publisher is None:
            raise RuntimeError("Stopping workbenches needs a publisher")
        if reason:
            pp.statusReason = reason
        command = initiate_provisioned_product_stop_command.InitiateProvisionedProductStopCommand(
            provisioned_product_id=provisioned_product_id_value_object.from_str(pp.provisionedProductId),
            project_id=project_id_value_object.from_str(pp.projectId),
            user_id=user_id_value_object.from_str(process_name, type=user_id_value_object.UserIdType.Service),
        )
        agg = provisioned_product_state_aggregate.ProvisionedProductStateAggregate(
            logger=self._logger, provisioned_product=pp
        )
        agg.initiate_stop_instance(command=command)
        self._publisher.publish(agg)


def _parse_time(value: str | None) -> datetime | None:
    """ISO timestamps as VEW stores them; None (or unparsable) means "unknown"."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
