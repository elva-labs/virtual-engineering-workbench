from typing import List, Optional

from pydantic import ConfigDict

from app.projects.domain.value_objects import project_id_value_object, user_id_value_object, user_role_value_object
from app.shared.adapters.message_bus import command_bus


class AssignUserCommand(command_bus.Command):
    project_id: project_id_value_object.ProjectIdValueObject
    user_id: user_id_value_object.UserIdValueObject
    roles: List[user_role_value_object.UserRoleValueObject]
    user_email: Optional[str] = None
    user_display_name: Optional[str] = None
    model_config = ConfigDict(arbitrary_types_allowed=True)
