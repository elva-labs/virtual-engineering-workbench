import logging
import typing
from datetime import datetime, timezone

from app.publishing.domain.command_handlers import retire_version_command_handler
from app.publishing.domain.commands import retire_version_command
from app.publishing.domain.events import product_version_creation_started
from app.publishing.domain.exceptions import domain_exception
from app.publishing.domain.model import portfolio, product, product_template, version
from app.publishing.domain.model.version import VersionReleaseType
from app.publishing.domain.ports import (
    iac_service,
    portfolios_query_service,
    template_service,
    versions_query_service,
)
from app.publishing.domain.query_services import distribution_portfolios, template_domain_query_service
from app.publishing.domain.read_models import component_version_detail
from app.publishing.domain.value_objects import (
    product_id_value_object,
    product_type_value_object,
    project_id_value_object,
    user_id_value_object,
    version_id_value_object,
)
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work
from app.shared.api import parameter_service

# The first version of a product, as for a version created in the portal (create_version_command_handler).
INITIAL_VERSION = version.format_version_name("1", "0", "0", version.VersionType.ReleaseCandidate, "1")


def _get_latest_version_name(
    versions_qry_srv: versions_query_service.VersionsQueryService,
    product_id: str,
) -> str | None:
    latest_version_name, _ = versions_qry_srv.get_latest_version_name_and_id(
        product_id=product_id, version_name_begins_with=None
    )
    return latest_version_name


def _calculate_new_version_name(latest_version_name: str, release_type: str) -> str:
    try:
        version_parts = latest_version_name.split(".")
        if len(version_parts) < 3:
            raise ValueError("Invalid version format")

        major = int(version_parts[0])
        minor = int(version_parts[1])
        patch_part = version_parts[2].split("-")[0] if "-" in version_parts[2] else version_parts[2]
        patch = int(patch_part)

        if release_type == VersionReleaseType.Major:
            new_major = str(major + 1)
            new_minor = "0"
            new_patch = "0"
        elif release_type == VersionReleaseType.Minor:
            new_major = str(major)
            new_minor = str(minor + 1)
            new_patch = "0"
        elif release_type == VersionReleaseType.Patch:
            new_major = str(major)
            new_minor = str(minor)
            new_patch = str(patch + 1)
        else:
            raise domain_exception.DomainException(f"Invalid release type: {release_type}")

        return version.format_version_name(new_major, new_minor, new_patch, version.VersionType.ReleaseCandidate, "1")

    except (ValueError, IndexError) as e:
        raise domain_exception.DomainException(f"Failed to parse version name {latest_version_name}: {str(e)}")


def _superseded_rc_versions(
    version_qry_srv: versions_query_service.VersionsQueryService, product_entity: product.Product
) -> list[version.Version]:
    """Release candidates a new build may replace, oldest first: in DEV only and not recommended.

    One entry per version (a version has a distribution per account)."""
    distributions = version_qry_srv.get_product_version_distributions(
        product_id=product_entity.productId, statuses=[version.VersionStatus.Created]
    )
    by_version: dict[str, list[version.Version]] = {}
    for distribution in distributions:
        by_version.setdefault(distribution.versionId, []).append(distribution)
    candidates = [
        dists[0]
        for version_id, dists in by_version.items()
        if version.VersionType.ReleaseCandidate.suffix in dists[0].versionName
        and all(d.stage == version.VersionStage.DEV for d in dists)
        and version_id != product_entity.recommendedVersionId
        and not any(d.isRecommendedVersion for d in dists)
    ]
    return sorted(candidates, key=lambda v: v.createDate)


def _retire_superseded_rc_versions(
    count: int,
    version_qry_srv: versions_query_service.VersionsQueryService,
    product_entity: product.Product,
    ami_id: str,
    user_id: str,
    uow: unit_of_work.UnitOfWork,
    message_bus: message_bus.MessageBus,
    logger: logging.Logger,
) -> bool:
    """Every build is a DEV release candidate; at the limit the oldest ones make room, as a person would
    retire them in the portal. False if there are not enough of them (nothing is retired then)."""
    candidates = _superseded_rc_versions(version_qry_srv, product_entity)
    if len(candidates) < count:
        return False
    for candidate in candidates[:count]:
        logger.info(f"Retiring release candidate {candidate.versionName} of {product_entity.productId} for {ami_id}")
        retire_version_command_handler.handle(
            command=retire_version_command.RetireVersionCommand(
                projectId=project_id_value_object.from_str(product_entity.projectId),
                productId=product_id_value_object.from_str(product_entity.productId),
                versionId=version_id_value_object.from_str(candidate.versionId),
                userRoles=[],
                retiredBy=user_id_value_object.from_str(user_id),
                retireReason=f"Superseded by the automated build of {ami_id} (release candidate limit reached)",
            ),
            uow=uow,
            message_bus=message_bus,
            versions_qry_srv=version_qry_srv,
        )
    return True


def _check_versions_limit(
    param_service: parameter_service.ParameterService,
    product_version_limit_param_name: str,
    version_qry_srv: versions_query_service.VersionsQueryService,
    product_entity: product.Product,
    product_rc_version_limit_param_name: str,
    make_room: typing.Callable[[int], bool] | None = None,
):
    version_limit = int(param_service.get_parameter_value(parameter_name=product_version_limit_param_name))

    number_of_versions = version_qry_srv.get_distinct_number_of_versions(
        product_id=product_entity.productId,
        status=version.VersionStatus.Created,
    )

    if number_of_versions >= version_limit:
        raise domain_exception.DomainException(
            "You have reached the maximum number of active versions for this product."
        )

    rc_version_limit = int(param_service.get_parameter_value(parameter_name=product_rc_version_limit_param_name))

    number_of_rc_versions = version_qry_srv.get_distinct_number_of_versions(
        product_id=product_entity.productId,
        status=version.VersionStatus.Created,
        version_name_filter=version.VersionType.ReleaseCandidate.suffix,
    )

    if number_of_rc_versions >= rc_version_limit and not (
        make_room and make_room(number_of_rc_versions - rc_version_limit + 1)
    ):
        raise domain_exception.DomainException(
            "You have reached the maximum number of active RC versions for this product."
        )


def _get_and_validate_product(uow: unit_of_work.UnitOfWork, project_id: str, product_id: str) -> product.Product:

    with uow:
        product_entity: product.Product = uow.get_repository(product.ProductPrimaryKey, product.Product).get(
            pk=product.ProductPrimaryKey(projectId=project_id, productId=product_id)
        )

    if product_entity.status != product.ProductStatus.Created:
        raise domain_exception.DomainException(
            "New product version can be created only from product with status 'Created'"
        )

    return product_entity


def _get_dev_portfolios(
    portf_qry_srv: portfolios_query_service.PortfoliosQueryService,
    product_entity: product.Product,
) -> typing.List[portfolio.Portfolio]:

    fetched_dev_portfolios = distribution_portfolios.target_portfolios(
        portf_qry_srv, product_entity, portfolio.PortfolioStage.DEV.value
    )
    if not fetched_dev_portfolios:
        raise domain_exception.DomainException("No portfolio found for DEV stage. Account setup might be incomplete.")
    return fetched_dev_portfolios


def _prepare_template(
    stack_srv: iac_service.IACService,
    template_domain_qry_srv: template_domain_query_service.TemplateDomainQueryService,
    file_service: template_service.TemplateService,
    product_entity: product.Product,
    product_id: str,
    version_id: str,
    project_id: str,
    architecture: str | None,
) -> typing.Tuple[str, typing.List]:
    template_content = template_domain_qry_srv.get_latest_draft_template(
        project_id=project_id_value_object.from_str(project_id),
        product_id=product_id_value_object.from_str(product_id),
    )

    # The draft stays a Jinja template (rendered at publish); CloudFormation validates it rendered.
    is_valid, parameters, error_message = stack_srv.validate_template(
        template_body=product_template.render_for_validation(template_content, architecture=architecture)
    )
    if not is_valid:
        raise domain_exception.DomainException(f"The template is invalid: {error_message}")

    template_file_name = template_domain_qry_srv.get_default_template_file_name(
        product_type=product_type_value_object.from_str(product_entity.productType.value),
        is_draft=True,
    )
    template_path = f"{product_id}/{version_id}/{template_file_name}"
    file_service.put_template(template_path=template_path, content=template_content.encode())

    return template_path, parameters


def _get_additional_attributes(
    product_entity: product.Product,
    ami_id: str,
    component_version_details: list[component_version_detail.ComponentVersionDetail],
    os_version: str,
    platform: str,
    architecture: str,
    integrations: list[str],
) -> dict:

    additional_attributes = {}
    if product_entity.productType == product.ProductType.Container:
        additional_attributes["imageTag"] = f"automated-{ami_id}"
        additional_attributes["imageDigest"] = f"sha256:{ami_id}"
    else:
        additional_attributes["originalAmiId"] = ami_id
        additional_attributes["componentVersionDetails"] = [cmp.model_dump() for cmp in component_version_details]
        additional_attributes["osVersion"] = os_version
        additional_attributes["platform"] = platform
        additional_attributes["architecture"] = architecture
        additional_attributes["integrations"] = integrations

    return additional_attributes


def _create_and_save_version(
    portf: portfolio.Portfolio,
    version_id: str,
    project_id: str,
    new_version_name: str,
    template_path: str,
    product_id: str,
    product_entity: product.Product,
    parameters: typing.List,
    additional_attributes: dict,
    ami_id: str,
    user_id: str,
    uow: unit_of_work.UnitOfWork,
    message_bus: message_bus.MessageBus,
) -> None:
    current_time = datetime.now(timezone.utc).isoformat()
    version_entity = version.Version(
        versionId=version_id,
        projectId=project_id,
        versionName=new_version_name,
        versionType=version.VersionType.ReleaseCandidate.text,
        draftTemplateLocation=template_path,
        scPortfolioId=portf.scPortfolioId,
        productId=product_id,
        versionDescription=f"Automated build product for image {ami_id}",
        technologyId=product_entity.technologyId,
        awsAccountId=portf.awsAccountId,
        accountId=portf.accountId,
        stage=version.VersionStage.DEV,
        region=portf.region,
        status=version.VersionStatus.Creating,
        isRecommendedVersion=False,
        parameters=parameters,
        createDate=current_time,
        lastUpdateDate=current_time,
        createdBy=user_id,
        lastUpdatedBy=user_id,
        **additional_attributes,
    )

    with uow:
        uow.get_repository(version.VersionPrimaryKey, version.Version).add(version_entity)
        uow.commit()

    message_bus.publish(
        product_version_creation_started.ProductVersionCreationStarted(
            product_id=version_entity.productId,
            version_id=version_entity.versionId,
            aws_account_id=version_entity.awsAccountId,
            stage=version_entity.stage,
            product_type=product_entity.productType,
        )
    )


def handle(
    ami_id: str,
    product_id: str,
    project_id: str,
    release_type: str,
    user_id: str,
    component_version_details: list[component_version_detail.ComponentVersionDetail],
    os_version: str,
    platform: str,
    architecture: str,
    integrations: list[str],
    template_domain_qry_srv: template_domain_query_service.TemplateDomainQueryService,
    logger: logging.Logger,
    uow: unit_of_work.UnitOfWork,
    message_bus: message_bus.MessageBus,
    portf_qry_srv: portfolios_query_service.PortfoliosQueryService,
    version_qry_srv: versions_query_service.VersionsQueryService,
    param_service: parameter_service.ParameterService,
    product_version_limit_param_name: str,
    product_rc_version_limit_param_name: str,
    stack_srv: iac_service.IACService,
    file_service: template_service.TemplateService,
    retire_superseded_rc_versions: bool = False,
) -> None:
    try:
        logger.info(f"Starting automated version creation for AMI {ami_id}, product {product_id}, project {project_id}")

        product_entity = _get_and_validate_product(uow, project_id, product_id)

        # A product linked to a pipeline gets its first version from the first build (this used to require
        # an existing version, so a new product never got one).
        latest_version_name = _get_latest_version_name(version_qry_srv, product_id)
        logger.info(f"Found latest version: {latest_version_name}")

        fetched_dev_portfolios = _get_dev_portfolios(portf_qry_srv, product_entity)

        # Opt-in: a build at the release candidate limit retires the oldest DEV-only candidates
        # instead of being dropped (a product built on every change reaches the limit quickly).
        make_room = (
            (
                lambda count: _retire_superseded_rc_versions(
                    count, version_qry_srv, product_entity, ami_id, user_id, uow, message_bus, logger
                )
            )
            if retire_superseded_rc_versions
            else None
        )
        _check_versions_limit(
            param_service,
            product_version_limit_param_name,
            version_qry_srv,
            product_entity,
            product_rc_version_limit_param_name,
            make_room,
        )

        version_id = version.generate_version_id()
        new_version_name = (
            _calculate_new_version_name(latest_version_name, release_type) if latest_version_name else INITIAL_VERSION
        )
        logger.info(f"New version name: {new_version_name}")

        template_path, parameters = _prepare_template(
            stack_srv,
            template_domain_qry_srv,
            file_service,
            product_entity,
            product_id,
            version_id,
            project_id,
            architecture if product_entity.productType != product.ProductType.Container else None,
        )

        additional_attributes = _get_additional_attributes(
            product_entity,
            ami_id,
            component_version_details,
            os_version,
            platform,
            architecture,
            integrations,
        )

        for portf in fetched_dev_portfolios:
            _create_and_save_version(
                portf,
                version_id,
                project_id,
                new_version_name,
                template_path,
                product_id,
                product_entity,
                parameters,
                additional_attributes,
                ami_id,
                user_id,
                uow,
                message_bus,
            )

        logger.info(
            f"Successfully created automated version {new_version_name} for AMI {ami_id}, product {product_id}, project {project_id}"
        )

    except domain_exception.DomainException:
        raise
    except Exception as e:
        raise domain_exception.DomainException(
            f"Failed to create automated product version for AMI {ami_id} and product {product_id}: {str(e)}"
        )
