"""Base image release channels of a deployment.

A deployment can let one project (typically a platform team's) build golden images on the raw OS and
release them as base images that every recipe can build on. Each architecture has two channels:

- test: the OS entry "<osVersion> (test)" - projects try the next base here;
- prod: the OS entry "<osVersion>" - what projects build on. A prod release takes only an image that
  is or was released to test for the same architecture.

A release writes the build's AMI to the channel's SSM parameter, which the OS entry of the system
configuration mapping resolves when a recipe version is created; the replaced value is kept in
"<parameter>/previous" for rollback. Configured in infra/config.py (packaging "base-images"); without a
releasing project the feature is off.
"""

import json
import os
from dataclasses import dataclass, field

CHANNELS = ("test", "prod")
# A channel that must have carried an image before this one takes it.
REQUIRES = {"prod": "test"}


@dataclass(frozen=True)
class BaseImageChannels:
    releasing_project_id: str = ""
    parameter_prefix: str = "/vew/base-images"
    os_version: str = ""
    source_os_version: str = "Ubuntu 24"
    platform: str = "Linux"
    architectures: tuple[str, ...] = field(default_factory=lambda: ("amd64", "arm64"))

    @property
    def enabled(self) -> bool:
        return bool(self.releasing_project_id and self.os_version)

    def os_version_of(self, channel: str) -> str:
        return self.os_version if channel == "prod" else f"{self.os_version} ({channel})"

    @property
    def os_versions(self) -> set[str]:
        return {self.os_version_of(c) for c in CHANNELS} if self.enabled else set()

    def parameter_name(self, channel: str, architecture: str) -> str:
        return f"{self.parameter_prefix}/{channel}/{architecture}"

    def parameters(self) -> dict[tuple[str, str], str]:
        """(channel, architecture) -> the parameter holding the released base image."""
        if not self.enabled:
            return {}
        return {(c, a): self.parameter_name(c, a) for c in CHANNELS for a in self.architectures}

    @staticmethod
    def from_dict(values: dict) -> "BaseImageChannels":
        return BaseImageChannels(
            releasing_project_id=values.get("releasingProjectId", ""),
            parameter_prefix=values.get("parameterPrefix", "/vew/base-images"),
            os_version=values.get("osVersion", ""),
            source_os_version=values.get("sourceOsVersion", "Ubuntu 24"),
            platform=values.get("platform", "Linux"),
            architectures=tuple(values.get("architectures", ("amd64", "arm64"))),
        )


def from_environment() -> BaseImageChannels:
    """The deployment's channels, handed to the packaging Lambdas as BASE_IMAGE_CHANNELS (JSON)."""
    return BaseImageChannels.from_dict(json.loads(os.environ.get("BASE_IMAGE_CHANNELS") or "{}"))
