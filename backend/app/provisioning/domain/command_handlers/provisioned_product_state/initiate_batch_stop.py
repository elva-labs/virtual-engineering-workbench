import logging

from app.provisioning.domain.aggregates import provisioned_product_state_aggregate
from app.provisioning.domain.aggregates.provisioned_product_state_aggregate import (
    PROVISIONED_PRODUCT_BATCH_STOP_PROCESS_NAME,
)
from app.provisioning.domain.commands.provisioned_product_state import (
    initiate_provisioned_product_batch_stop_command,
    initiate_provisioned_product_stop_command,
)
from app.provisioning.domain.model import product_status
from app.provisioning.domain.ports import provisioned_products_query_service
from app.provisioning.domain.value_objects import (
    project_id_value_object,
    provisioned_product_id_value_object,
    user_id_value_object,
)
from app.shared.adapters.feature_toggling import product_feature_toggles
from app.shared.ddd import aggregate


def handle(
    command: initiate_provisioned_product_batch_stop_command.InitiateProvisionedProductBatchStopCommand,
    publisher: aggregate.AggregatePublisher,
    logger: logging.Logger,
    pp_qry_srv: provisioned_products_query_service.ProvisionedProductsQueryService,
    lifecycle_srv=None,
):
    # The program's workbench stop rules: weekendStop off or alwaysOn keep them running.
    programs = lifecycle_srv.programs() if lifecycle_srv else {}
    # Loop through all running provisioned products
    for pp_entity in pp_qry_srv.get_all_provisioned_products(status=product_status.ProductStatus.Running):
        if lifecycle_srv and not lifecycle_srv.weekend_stop_allowed(pp_entity, programs):
            continue
        # Check if the provisioned product has auto stop protection
        feature_toggles = product_feature_toggles.ProductFeatureToggles(outputs=pp_entity.outputs)
        if feature_toggles.is_enabled(product_feature_toggles.ProductFeature.AutoStopProtection):
            continue

        # Prepare stop command
        stop_command = initiate_provisioned_product_stop_command.InitiateProvisionedProductStopCommand(
            provisioned_product_id=provisioned_product_id_value_object.from_str(pp_entity.provisionedProductId),
            project_id=project_id_value_object.from_str(pp_entity.projectId),
            user_id=user_id_value_object.from_str(PROVISIONED_PRODUCT_BATCH_STOP_PROCESS_NAME),
        )

        # Initiate stopping the provisioned product
        pp_state_aggregate = provisioned_product_state_aggregate.ProvisionedProductStateAggregate(
            logger=logger, provisioned_product=pp_entity
        )
        pp_state_aggregate.initiate_stop_instance(command=stop_command)
        publisher.publish(pp_state_aggregate)
