from app.packaging.domain.value_objects.component import (
    component_platform_value_object,
    component_supported_architecture_value_object,
    component_supported_os_version_value_object,
)
from app.shared.adapters.message_bus import command_bus


class DeleteMandatoryComponentsListCommand(command_bus.Command):
    mandatoryComponentsListPlatform: component_platform_value_object.ComponentPlatformValueObject
    mandatoryComponentsListOsVersion: component_supported_os_version_value_object.ComponentSupportedOsVersionValueObject
    mandatoryComponentsListArchitecture: (
        component_supported_architecture_value_object.ComponentSupportedArchitectureValueObject
    )
