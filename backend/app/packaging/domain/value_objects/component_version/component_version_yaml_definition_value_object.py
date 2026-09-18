import typing
from dataclasses import dataclass
from typing import Annotated, Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.packaging.domain.exceptions import domain_exception


# Pydantic validation of EC2 Image Builder component YAML definition
# Ref: https://docs.aws.amazon.com/imagebuilder/latest/userguide/toe-use-documents.html
class ComponentStepYaml(BaseModel):
    name: str
    action: str
    timeoutSeconds: Optional[int] = 7200
    onFailure: Optional[Literal["Abort", "Continue", "Failed"]] = "Abort"
    maxAttempts: Optional[int] = 1
    inputs: Optional[Union[List, Dict]] = (
        None  # This is not really required for all actions
    )


class ComponentPhaseYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["build", "test", "validate"]
    steps: Annotated[List[ComponentStepYaml], Field(min_length=1)]


class ComponentConstantYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string"]
    value: Optional[str] = None


class ComponentParameterYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string"]
    default: Optional[str] = None
    description: Optional[str] = None


class ComponentYaml(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    name: Optional[str] = None
    description: Optional[str] = None
    schemaVersion: str
    constants: Optional[List[Dict[str, ComponentConstantYaml]]] = None
    parameters: Optional[List[Dict[str, ComponentParameterYaml]]] = None
    phases: List[ComponentPhaseYaml]


@dataclass(frozen=True)
class ComponentVersionYamlDefinitionValueObject:
    value: str


def from_str(value: typing.Optional[str]) -> ComponentVersionYamlDefinitionValueObject:
    if not value:
        raise domain_exception.DomainException(
            "Component version YAML definition cannot be empty."
        )

    try:
        to_dict(value)
    except domain_exception.DomainException as error:
        raise domain_exception.DomainException(
            "Component version YAML definition is invalid."
        ) from error

    return ComponentVersionYamlDefinitionValueObject(value=value)


def from_dict(value: dict) -> ComponentVersionYamlDefinitionValueObject:
    try:
        model = ComponentYaml.model_validate(value)
        if not model.phases:
            raise ValueError("phases must not be empty")
    except ValidationError as error:
        raise domain_exception.DomainException(
            "Component version definition is invalid."
        ) from error
    except ValueError as error:
        raise domain_exception.DomainException(
            "Component version definition is invalid."
        ) from error
    canonical = model.model_dump(mode="json", exclude_none=True)
    return ComponentVersionYamlDefinitionValueObject(
        value=yaml.safe_dump(canonical, sort_keys=False)
    )


def to_dict(value: str | bytes) -> dict:
    try:
        model = ComponentYaml.model_validate(yaml.safe_load(value))
    except (TypeError, yaml.YAMLError, ValidationError) as error:
        raise domain_exception.DomainException(
            "Component version definition is invalid."
        ) from error
    return model.model_dump(mode="json", exclude_none=True)
