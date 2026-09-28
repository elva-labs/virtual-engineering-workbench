from typing import Optional

from pydantic import ConfigDict

from app.projects.domain.value_objects import project_id_value_object
from app.shared.adapters.message_bus import command_bus


class AddTechnologyCommand(command_bus.Command):
    name: str
    description: Optional[str] = None
    project_id: project_id_value_object.ProjectIdValueObject
    technology_id: Optional[str] = None
    model_config = ConfigDict(arbitrary_types_allowed=True)
