from typing import Literal

from app.packaging.domain.value_objects.image import image_id_value_object
from app.packaging.domain.value_objects.shared import project_id_value_object, user_id_value_object
from app.shared.adapters.message_bus import command_bus


class ReleaseBaseImageCommand(command_bus.Command):
    projectId: project_id_value_object.ProjectIdValueObject
    imageId: image_id_value_object.ImageIdValueObject
    architecture: Literal["amd64", "arm64"]
    channel: Literal["test", "prod"]
    releasedBy: user_id_value_object.UserIdValueObject
