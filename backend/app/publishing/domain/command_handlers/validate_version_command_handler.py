from app.publishing.domain.commands import validate_version_command
from app.publishing.domain.exceptions import domain_exception
from app.publishing.domain.model import product_template
from app.publishing.domain.ports import iac_service


def handle(
    command: validate_version_command.ValidateVersionCommand,
    stack_srv: iac_service.IACService,
):

    # Validate the template: a draft is a Jinja template (rendered at publish), validated rendered.
    is_valid, params, error_message = stack_srv.validate_template(
        template_body=product_template.render_for_validation(command.versionTemplateDefinition.value)
    )
    if not is_valid:
        raise domain_exception.DomainException(f"The template is invalid: {error_message}")
