from abc import ABC, abstractmethod


class BaseImageParameterService(ABC):
    """The SSM parameters holding the released base images (app/packaging/domain/model/recipe/base_image_channels.py)."""

    @abstractmethod
    def get_parameter_value(self, name: str) -> str | None:
        """The parameter's value, or None if it does not exist."""

    @abstractmethod
    def get_parameter_history(self, name: str) -> list[str]:
        """Every value the parameter ever had, oldest first; empty if it does not exist."""

    @abstractmethod
    def put_parameter_value(self, name: str, value: str, description: str) -> None:
        """Creates the parameter or overwrites its value."""


class ImageTagService(ABC):
    """The vew:base-channel tag on released AMIs."""

    @abstractmethod
    def set_base_channels(self, ami_id: str, channels: set[str]) -> None:
        """Tags the AMI with the channels it serves; removes the tag when it serves none."""
