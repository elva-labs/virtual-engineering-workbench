"""Rendering of product templates.

Product templates are Jinja templates: the image per region (RegionMap) and the instance types of
the image's architecture are rendered when a version is published to an account. A draft is
therefore not valid CloudFormation until rendered, so validation renders it with placeholders.
"""

import jinja2
from jinja2.sandbox import SandboxedEnvironment

from app.publishing.domain.exceptions import domain_exception

# Stands in for the account's image while a draft is validated (before any image is shared).
VALIDATION_REGION_KEY = "Validation"
VALIDATION_AMI_ID = "ami-00000000000000000"


def render(
    template: str,
    product_name: str,
    product_version: str,
    ami_ids: dict[str, str],
    architecture: str | None,
) -> str:
    return (
        SandboxedEnvironment(loader=jinja2.BaseLoader())
        .from_string(template)
        .render(
            product_name=product_name,
            product_version=product_version,
            ami_ids=ami_ids,
            # The templates offer the instance family of the image's architecture (arm64 or x86_64).
            architecture=architecture,
        )
    )


def render_for_validation(template: str, architecture: str | None = None) -> str:
    """The draft as CloudFormation will see it; a template without Jinja is returned unchanged.

    The architecture decides the InstanceType parameter's allowed values, which validation returns
    as the version's parameters, so pass the version's image architecture where it is known.
    """
    try:
        return render(
            template,
            product_name="validation",
            product_version="0.0.0",
            ami_ids={VALIDATION_REGION_KEY: VALIDATION_AMI_ID},
            architecture=architecture,
        )
    except jinja2.TemplateError as e:
        raise domain_exception.DomainException(f"The template is invalid: {e}")
