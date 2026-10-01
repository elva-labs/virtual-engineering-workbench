from app.packaging.domain.commands.component import delete_mandatory_components_list_command
from app.packaging.domain.model.component import mandatory_components_list
from app.packaging.domain.ports import mandatory_components_list_query_service
from app.shared.adapters.unit_of_work_v2 import unit_of_work


def handle(
    command: delete_mandatory_components_list_command.DeleteMandatoryComponentsListCommand,
    mandatory_components_list_qry_srv: mandatory_components_list_query_service.MandatoryComponentsListQueryService,
    uow: unit_of_work.UnitOfWork,
) -> bool:
    """Removes the list; deleting a list that does not exist is not an error (an idempotent DELETE).

    Returns whether a list was removed. Recipe versions already created keep the components they got;
    only recipe versions created afterwards stop receiving them.
    """
    platform = command.mandatoryComponentsListPlatform.value
    os_version = command.mandatoryComponentsListOsVersion.value
    architecture = command.mandatoryComponentsListArchitecture.value

    if (
        mandatory_components_list_qry_srv.get_mandatory_components_list(
            platform=platform, os=os_version, architecture=architecture
        )
        is None
    ):
        return False

    with uow:
        uow.get_repository(
            mandatory_components_list.MandatoryComponentsListPrimaryKey,
            mandatory_components_list.MandatoryComponentsList,
        ).remove(
            mandatory_components_list.MandatoryComponentsListPrimaryKey(
                mandatoryComponentsListPlatform=platform,
                mandatoryComponentsListOsVersion=os_version,
                mandatoryComponentsListArchitecture=architecture,
            )
        )
        uow.commit()
    return True
