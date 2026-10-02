"""Base image release channels of a deployment.

A deployment can let one project (typically a platform team's) build golden images on the raw OS and
release them as base images that every recipe can build on. Each architecture has two channels:

- test: projects try the next base here;
- prod: what projects build on. A prod release takes only an image that is or was released to test for
  the same architecture.

Recipes build on the OS entry "<osVersion>", and each recipe version picks the channel its parent image
comes from (baseImageChannel, prod by default), so trying the next base and moving back to prod are
recipe version bumps on the same recipe and pipeline. The OS entry "<osVersion> (test)" is deprecated:
it is still accepted and its versions always build on test.

A release writes the build's AMI to the channel's SSM parameter, which a recipe version resolves when it
is created or updated; the replaced value is kept in "<parameter>/previous" for rollback. Configured in infra/config.py (packaging "base-images"); without a
releasing project the feature is off.
"""

import json
import os
from dataclasses import dataclass, field

from app.packaging.domain.exceptions import domain_exception

CHANNELS = ("test", "prod")
DEFAULT_CHANNEL = "prod"
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

    def entry_channel(self, os_version: str) -> str | None:
        """The channel a deprecated per-channel OS entry ("<osVersion> (test)") always builds on."""
        return next(
            (c for c in CHANNELS if c != DEFAULT_CHANNEL and self.enabled and os_version == self.os_version_of(c)),
            None,
        )

    def resolve_channel(self, os_version: str, requested: str | None) -> str | None:
        """The channel a new or updated recipe version builds on, or None for a recipe outside the base entries."""
        if requested is not None and requested not in CHANNELS:
            raise domain_exception.DomainException(
                f"Base image channel {requested} is unknown; use one of {sorted(CHANNELS)}."
            )
        if (channel := self.entry_channel(os_version)) is not None:
            if requested not in (None, channel):
                raise domain_exception.DomainException(
                    f"{os_version} always builds on the {channel} channel. Use {self.os_version} with "
                    f"baseImageChannel {requested} instead."
                )
            return channel
        if os_version in self.os_versions:
            return requested or DEFAULT_CHANNEL
        if requested is not None:
            raise domain_exception.DomainException(
                f"baseImageChannel only applies to recipes on {self.os_version or 'a base image'}, not {os_version}."
            )
        return None

    def effective_channel(self, os_version: str, stored: str | None) -> str | None:
        """The channel a stored recipe version builds on; versions created before the field built on their entry's."""
        if stored is not None:
            return stored
        if (channel := self.entry_channel(os_version)) is not None:
            return channel
        return DEFAULT_CHANNEL if os_version in self.os_versions else None

    def parent_image_parameter(
        self, system_configuration_mapping: dict, platform: str, architecture: str, os_version: str, channel: str | None
    ) -> str | None:
        """The SSM parameter of a recipe version's parent image: the channel's for a base recipe, otherwise the OS
        entry's own."""
        if channel is not None:
            return self.parameter_name(channel, architecture)
        return (
            system_configuration_mapping.get(platform, {})
            .get(architecture, {})
            .get(os_version, {})
            .get("ami_ssm_param_name")
        )

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
