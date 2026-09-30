from typing import Any, Callable

from app.packaging.domain.ports import base_image_release_service


class SSMBaseImageParameterService(base_image_release_service.BaseImageParameterService):
    """The base image parameters in the AMI factory account, where recipe versions resolve their parent.

    The parameters are created on the first release rather than by CloudFormation, which would reset a
    released value on the next deployment.
    """

    def __init__(self, client_factory: Callable[[str], Any]):
        self._client_factory = client_factory

    def get_parameter_value(self, name: str) -> str | None:
        client = self._client_factory("ssm")
        try:
            return client.get_parameter(Name=name)["Parameter"]["Value"]
        except client.exceptions.ParameterNotFound:
            return None

    def get_parameter_history(self, name: str) -> list[str]:
        client = self._client_factory("ssm")
        values: list[str] = []
        try:
            for page in client.get_paginator("get_parameter_history").paginate(Name=name):
                values.extend(item["Value"] for item in page["Parameters"])
        except client.exceptions.ParameterNotFound:
            return []
        return values

    def put_parameter_value(self, name: str, value: str, description: str) -> None:
        self._client_factory("ssm").put_parameter(
            Name=name, Value=value, Type="String", Description=description, Overwrite=True
        )
