from pydantic import ConfigDict

from app.projects.domain.model import project_account
from app.projects.domain.value_objects import (
    account_description_value_object,
    account_id_value_object,
    account_name_value_object,
    account_technology_id_value_object,
    account_type_value_object,
    project_id_value_object,
    region_value_object,
)
from app.shared.adapters.message_bus import command_bus


class UpdateProjectAccountCommand(command_bus.Command):
    project_id: project_id_value_object.ProjectIdValueObject
    account_id: account_id_value_object.AccountIdValueObject
    account_name: account_name_value_object.AccountNameValueObject
    account_description: account_description_value_object.AccountDescriptionValueObject
    account_type: account_type_value_object.AccountTypeValueObject
    technology: account_technology_id_value_object.AccountTechnologyIdValueObject
    stage: project_account.ProjectAccountStageEnum
    region: region_value_object.RegionValueObject
    # None leaves the stored revision alone (portal updates); a different value re-onboards.
    onboarding_revision: str | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)
