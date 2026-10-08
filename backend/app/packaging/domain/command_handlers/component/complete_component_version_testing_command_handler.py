from itertools import product

from app.packaging.domain.commands.component import complete_component_version_testing_command
from app.packaging.domain.events.recipe import recipe_version_update_on_component_update_requested
from app.packaging.domain.exceptions import domain_exception
from app.packaging.domain.model.component import component_version, component_version_test_execution
from app.packaging.domain.ports import (
    component_query_service,
    component_version_query_service,
    component_version_test_execution_query_service,
    component_version_testing_service,
)
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def __is_released_or_retired(
    command: complete_component_version_testing_command.CompleteComponentVersionTestingCommand,
    component_version_qry_srv: component_version_query_service.ComponentVersionQueryService,
) -> bool:
    current_entity = component_version_qry_srv.get_component_version(
        component_id=command.componentId.value, version_id=command.componentVersionId.value
    )
    return current_entity is not None and current_entity.status in (
        component_version.ComponentVersionStatus.Released,
        component_version.ComponentVersionStatus.Retired,
    )


def __count_statuses(test_command_statuses: list) -> dict:
    counts = {
        component_version_test_execution.ComponentVersionTestExecutionCommandStatus.Failed: 0,
        component_version_test_execution.ComponentVersionTestExecutionCommandStatus.Pending: 0,
        component_version_test_execution.ComponentVersionTestExecutionCommandStatus.Running: 0,
        component_version_test_execution.ComponentVersionTestExecutionCommandStatus.Success: 0,
    }
    for test_command_status in test_command_statuses:
        if test_command_status:
            counts[test_command_status] += 1
    return counts


def handle(
    command: complete_component_version_testing_command.CompleteComponentVersionTestingCommand,
    component_qry_srv: component_query_service.ComponentQueryService,
    component_version_qry_srv: component_version_query_service.ComponentVersionQueryService,
    component_version_test_execution_qry_srv: component_version_test_execution_query_service.ComponentVersionTestExecutionQueryService,
    component_version_testing_srv: component_version_testing_service.ComponentVersionTestingService,
    uow: unit_of_work.UnitOfWork,
    message_bus: message_bus.MessageBus,
):
    component_entity = component_qry_srv.get_component(command.componentId.value)

    if component_entity is None:
        raise domain_exception.DomainException(f"Component {command.componentId.value} does not exist.")

    component_version_test_execution_entities = (
        component_version_test_execution_qry_srv.get_component_version_test_executions_by_test_execution_id(
            version_id=command.componentVersionId.value, test_execution_id=command.testExecutionId.value
        )
    )
    instance_ids = [
        component_version_test_execution_entity.instanceId
        for component_version_test_execution_entity in component_version_test_execution_entities
    ]
    test_command_statuses = [
        component_version_test_execution_entity.testCommandStatus
        for component_version_test_execution_entity in component_version_test_execution_entities
    ]

    # First we teardown all the testing environments
    for instance_id in instance_ids:
        component_version_testing_srv.teardown_testing_environment(instance_id=instance_id)

    test_status = component_version_test_execution.ComponentVersionTestStatus.Failed
    aggregated_test_command_status_count = __count_statuses(test_command_statuses)

    # Then we set the component version test status to SUCCESS if test executions
    # for all supported architecture/OS version combinations have been successful
    if aggregated_test_command_status_count.get(
        component_version_test_execution.ComponentVersionTestExecutionCommandStatus.Success
    ) == len(
        list(product(component_entity.componentSupportedArchitectures, component_entity.componentSupportedOsVersions))
    ):
        test_status = component_version_test_execution.ComponentVersionTestStatus.Success

    status = (
        component_version.ComponentVersionStatus.Validated
        if test_status == component_version_test_execution.ComponentVersionTestStatus.Success
        else component_version.ComponentVersionStatus.Failed
    )
    # A late or duplicate test run (a retried creation event starts a second one) must not take a released
    # or retired version back to VALIDATED: the recipe release then refuses it as unreleased (2026-10-08).
    if __is_released_or_retired(command, component_version_qry_srv):
        return test_status

    # Finally we update the component version status
    with uow:
        uow.get_repository(
            component_version.ComponentVersionPrimaryKey, component_version.ComponentVersion
        ).update_attributes(
            component_version.ComponentVersionPrimaryKey(
                componentId=command.componentId.value,
                componentVersionId=command.componentVersionId.value,
            ),
            status=status,
        )
        uow.commit()
    if status == component_version.ComponentVersionStatus.Validated:
        component_version_entity = component_version_qry_srv.get_component_version(
            component_id=command.componentId.value, version_id=command.componentVersionId.value
        )
        if not component_version_entity:
            raise domain_exception.DomainException(
                f"Version {command.componentVersionId} of component {command.componentId} does not exist."
            )
        message_bus.publish(
            recipe_version_update_on_component_update_requested.RecipeVersionUpdateOnComponentUpdateRequested(
                component_id=component_version_entity.componentId,
                component_version_id=component_version_entity.componentVersionId,
                last_updated_by=component_version_entity.lastUpdatedBy,
            )
        )
    return test_status
