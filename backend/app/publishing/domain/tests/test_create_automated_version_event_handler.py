from unittest.mock import Mock, patch

import pytest
from freezegun import freeze_time

from app.publishing.domain.event_handlers import create_automated_version_event_handler
from app.publishing.domain.events import product_version_creation_started
from app.publishing.domain.exceptions import domain_exception
from app.publishing.domain.model import portfolio, product, version
from app.publishing.domain.read_models import component_version_detail


# Helper to create default component details for tests
def get_default_component_details():
    return [
        component_version_detail.ComponentVersionDetail(
            componentName="VS Code",
            componentVersionType=component_version_detail.ComponentVersionEntryType.Main,
            softwareVendor="Microsoft",
            softwareVersion="1.87.0",
        )
    ]


# Helper to call handler with common parameters
def call_handler(
    ami_id,
    product_id,
    project_id,
    release_type,
    mock_template_domain_qry_srv,
    mock_logger,
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    component_details=None,
    os_version="Ubuntu 24.04",
    platform="Linux",
    architecture="x86_64",
    integrations=None,
):
    if component_details is None:
        component_details = get_default_component_details()
    if integrations is None:
        integrations = ["GitHub"]

    create_automated_version_event_handler.handle(
        ami_id=ami_id,
        product_id=product_id,
        project_id=project_id,
        release_type=release_type,
        user_id="T123456",
        component_version_details=component_details,
        os_version=os_version,
        platform=platform,
        architecture=architecture,
        integrations=integrations,
        template_domain_qry_srv=mock_template_domain_qry_srv,
        logger=mock_logger,
        uow=mock_unit_of_work,
        message_bus=mock_message_bus,
        portf_qry_srv=mock_portfolios_qry_srv,
        version_qry_srv=mock_versions_qry_srv,
        param_service=mock_param_service,
        product_version_limit_param_name="version-limit",
        product_rc_version_limit_param_name="rc-limit",
        stack_srv=mock_stack_srv,
        file_service=mock_file_service,
    )


@patch(
    "app.publishing.domain.model.version.generate_version_id",
    return_value="vers-11111111",
)
@freeze_time("2023-06-20")
def test_handle_creates_automated_version_successfully_for_workbench_product(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_product_repo = Mock()
    mock_product_repo.get.return_value = mock_product_entity

    mock_repo = Mock()

    def get_repository_side_effect(pk_param, entity_param):
        if entity_param == product.Product:
            return mock_product_repo
        return mock_repo

    mock_unit_of_work.get_repository.side_effect = get_repository_side_effect

    # ACT
    call_handler(
        ami_id,
        product_id,
        project_id,
        "MINOR",
        mock_template_domain_qry_srv,
        mock_logger,
        mock_unit_of_work,
        mock_message_bus,
        mock_portfolios_qry_srv,
        mock_versions_qry_srv,
        mock_param_service,
        mock_stack_srv,
        mock_file_service,
    )

    # ASSERT
    mock_versions_qry_srv.get_latest_version_name_and_id.assert_called_once_with(
        product_id=product_id, version_name_begins_with=None
    )
    mock_portfolios_qry_srv.get_portfolios_by_tech_and_stage.assert_called_once_with(
        "tech-789", portfolio.PortfolioStage.DEV.value
    )
    mock_param_service.get_parameter_value.assert_called()
    mock_template_domain_qry_srv.get_latest_draft_template.assert_called_once()
    mock_stack_srv.validate_template.assert_called_once()
    mock_file_service.put_template.assert_called_once()
    mock_repo.add.assert_called_once()
    mock_message_bus.publish.assert_called_once()

    added_version = mock_repo.add.call_args[0][0]
    assert added_version.versionId == "vers-11111111"
    assert added_version.versionName == "1.1.0-rc.1"
    assert added_version.originalAmiId == ami_id
    assert added_version.status == version.VersionStatus.Creating
    assert added_version.createdBy == "T123456"

    published_event = mock_message_bus.publish.call_args[0][0]
    assert isinstance(published_event, product_version_creation_started.ProductVersionCreationStarted)
    assert published_event.product_id == product_id
    assert published_event.version_id == "vers-11111111"


@patch(
    "app.publishing.domain.model.version.generate_version_id",
    return_value="vers-11111111",
)
@freeze_time("2023-06-20")
def test_handle_creates_automated_version_successfully_for_container_product(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_container_product_entity,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_repo = Mock()
    mock_repo.get.return_value = mock_container_product_entity
    mock_unit_of_work.get_repository.return_value = mock_repo

    mock_versions_qry_srv.get_latest_version_name_and_id.return_value = (
        "1.0.0",
        "vers-12345",
    )

    # ACT
    call_handler(
        ami_id,
        product_id,
        project_id,
        "MAJOR",
        mock_template_domain_qry_srv,
        mock_logger,
        mock_unit_of_work,
        mock_message_bus,
        mock_portfolios_qry_srv,
        mock_versions_qry_srv,
        mock_param_service,
        mock_stack_srv,
        mock_file_service,
    )

    # ASSERT
    added_version = mock_repo.add.call_args[0][0]
    assert added_version.versionId == "vers-11111111"
    assert added_version.versionName == "2.0.0-rc.1"
    assert added_version.imageTag == "automated-ami-12345678"
    assert added_version.imageDigest == "sha256:ami-12345678"
    assert added_version.originalAmiId is None
    assert added_version.componentVersionDetails is None


@patch(
    "app.publishing.domain.model.version.generate_version_id",
    return_value="vers-11111111",
)
@freeze_time("2023-06-20")
def test_handle_creates_initial_version_when_product_has_no_version(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_product_repo = Mock()
    mock_product_repo.get.return_value = mock_product_entity

    mock_repo = Mock()

    def get_repository_side_effect(pk_param, entity_param):
        if entity_param == product.Product:
            return mock_product_repo
        return mock_repo

    mock_unit_of_work.get_repository.side_effect = get_repository_side_effect

    mock_versions_qry_srv.get_latest_version_name_and_id.return_value = (None, None)

    # ACT
    call_handler(
        ami_id,
        product_id,
        project_id,
        "PATCH",
        mock_template_domain_qry_srv,
        mock_logger,
        mock_unit_of_work,
        mock_message_bus,
        mock_portfolios_qry_srv,
        mock_versions_qry_srv,
        mock_param_service,
        mock_stack_srv,
        mock_file_service,
    )

    # ASSERT: the first build of a new product becomes its first version, as in the portal
    added_version = mock_repo.add.call_args[0][0]
    assert added_version.versionName == "1.0.0-rc.1"
    assert added_version.originalAmiId == ami_id
    assert added_version.stage == version.VersionStage.DEV
    mock_message_bus.publish.assert_called_once()


def test_handle_raises_exception_when_product_not_created(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    non_created_product = product.Product(
        projectId="project-123",
        productId="product-456",
        productName="Test Container Product",
        productType=product.ProductType.Container,
        status=product.ProductStatus.Creating,
        technologyId="tech-789",
        technologyName="Test Technology",
        createDate="2025-07-30T13:35:02.425779+00:00",
        lastUpdateDate="2025-07-30T13:35:02.425785+00:00",
        createdBy="user-123",
        lastUpdatedBy="user-123",
    )

    mock_product_repo = Mock()
    mock_product_repo.get.return_value = non_created_product

    def get_repository_side_effect(pk_param, entity_param):
        if entity_param == product.Product:
            return mock_product_repo
        return Mock()

    mock_unit_of_work.get_repository.side_effect = get_repository_side_effect

    # ACT & ASSERT
    with pytest.raises(
        domain_exception.DomainException,
        match="New product version can be created only from product with status 'Created'",
    ):
        call_handler(
            ami_id,
            product_id,
            project_id,
            "MINOR",
            mock_template_domain_qry_srv,
            mock_logger,
            mock_unit_of_work,
            mock_message_bus,
            mock_portfolios_qry_srv,
            mock_versions_qry_srv,
            mock_param_service,
            mock_stack_srv,
            mock_file_service,
        )


def test_handle_raises_exception_when_no_dev_portfolios(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_product_repo = Mock()
    mock_product_repo.get.return_value = mock_product_entity

    def get_repository_side_effect(pk_param, entity_param):
        if entity_param == product.Product:
            return mock_product_repo
        return Mock()

    mock_unit_of_work.get_repository.side_effect = get_repository_side_effect

    mock_portfolios_qry_srv.get_portfolios_by_tech_and_stage.return_value = []

    # ACT & ASSERT
    with pytest.raises(domain_exception.DomainException, match="No portfolio found for DEV stage"):
        call_handler(
            ami_id,
            product_id,
            project_id,
            "PATCH",
            mock_template_domain_qry_srv,
            mock_logger,
            mock_unit_of_work,
            mock_message_bus,
            mock_portfolios_qry_srv,
            mock_versions_qry_srv,
            mock_param_service,
            mock_stack_srv,
            mock_file_service,
        )


def test_handle_raises_exception_when_version_limit_exceeded(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_product_repo = Mock()
    mock_product_repo.get.return_value = mock_product_entity

    def get_repository_side_effect(pk_param, entity_param):
        if entity_param == product.Product:
            return mock_product_repo
        return Mock()

    mock_unit_of_work.get_repository.side_effect = get_repository_side_effect

    def count_version(product_id: str, status=None, version_name_filter=None):
        if version_name_filter:
            return 1
        return 10

    mock_versions_qry_srv.get_distinct_number_of_versions.side_effect = count_version

    def mock_get_parameter_value(parameter_name):
        if "version_limit" in parameter_name or "version-limit" in parameter_name:
            return "5"
        else:
            return "2"

    mock_param_service.get_parameter_value.side_effect = mock_get_parameter_value

    # ACT & ASSERT
    with pytest.raises(
        domain_exception.DomainException,
        match="You have reached the maximum number of active versions for this product",
    ):
        call_handler(
            ami_id,
            product_id,
            project_id,
            "MAJOR",
            mock_template_domain_qry_srv,
            mock_logger,
            mock_unit_of_work,
            mock_message_bus,
            mock_portfolios_qry_srv,
            mock_versions_qry_srv,
            mock_param_service,
            mock_stack_srv,
            mock_file_service,
        )


def test_handle_raises_exception_when_rc_version_limit_exceeded(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_product_repo = Mock()
    mock_product_repo.get.return_value = mock_product_entity

    def get_repository_side_effect(pk_param, entity_param):
        if entity_param == product.Product:
            return mock_product_repo
        return Mock()

    mock_unit_of_work.get_repository.side_effect = get_repository_side_effect

    def count_version(product_id: str, status=None, version_name_filter=None):
        if version_name_filter:
            return 5
        return 3

    mock_versions_qry_srv.get_distinct_number_of_versions.side_effect = count_version

    def mock_get_parameter_value(parameter_name):
        if "version-limit" in parameter_name:
            return "10"
        elif "rc-limit" in parameter_name:
            return "2"
        else:
            return "10"

    mock_param_service.get_parameter_value.side_effect = mock_get_parameter_value

    # ACT & ASSERT
    with pytest.raises(
        domain_exception.DomainException,
        match="You have reached the maximum number of active RC versions for this product",
    ):
        call_handler(
            ami_id,
            product_id,
            project_id,
            "MINOR",
            mock_template_domain_qry_srv,
            mock_logger,
            mock_unit_of_work,
            mock_message_bus,
            mock_portfolios_qry_srv,
            mock_versions_qry_srv,
            mock_param_service,
            mock_stack_srv,
            mock_file_service,
        )


def test_handle_raises_exception_when_template_invalid(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_product_repo = Mock()
    mock_product_repo.get.return_value = mock_product_entity

    def get_repository_side_effect(pk_param, entity_param):
        if entity_param == product.Product:
            return mock_product_repo
        return Mock()

    mock_unit_of_work.get_repository.side_effect = get_repository_side_effect

    mock_template_domain_qry_srv.get_latest_draft_template.return_value = "invalid template content"
    mock_stack_srv.validate_template.return_value = (
        False,
        None,
        "Template validation error",
    )

    # ACT & ASSERT
    with pytest.raises(domain_exception.DomainException, match="The template is invalid"):
        call_handler(
            ami_id,
            product_id,
            project_id,
            "MAJOR",
            mock_template_domain_qry_srv,
            mock_logger,
            mock_unit_of_work,
            mock_message_bus,
            mock_portfolios_qry_srv,
            mock_versions_qry_srv,
            mock_param_service,
            mock_stack_srv,
            mock_file_service,
        )


@pytest.mark.parametrize(
    "latest_version_name,expected_new_version",
    [
        ("1.0.0", "1.0.1-rc.1"),
        ("2.5.10", "2.5.11-rc.1"),
        ("1.2.3-rc.1", "1.2.4-rc.1"),
        ("3.0.0-rc.5", "3.0.1-rc.1"),
    ],
)
@patch(
    "app.publishing.domain.model.version.generate_version_id",
    return_value="vers-11111111",
)
@freeze_time("2023-06-20")
def test_handle_calculates_correct_patch_version_name(
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
    latest_version_name,
    expected_new_version,
):
    # ARRANGE
    ami_id = "ami-12345678"
    product_id = "product-456"
    project_id = "project-123"

    mock_repo = Mock()
    mock_repo.get.return_value = mock_product_entity
    mock_unit_of_work.get_repository.return_value = mock_repo

    mock_versions_qry_srv.get_latest_version_name_and_id.return_value = (
        latest_version_name,
        "vers-12345",
    )

    # ACT
    call_handler(
        ami_id,
        product_id,
        project_id,
        "PATCH",
        mock_template_domain_qry_srv,
        mock_logger,
        mock_unit_of_work,
        mock_message_bus,
        mock_portfolios_qry_srv,
        mock_versions_qry_srv,
        mock_param_service,
        mock_stack_srv,
        mock_file_service,
    )

    # ASSERT
    added_version = mock_repo.add.call_args[0][0]
    assert added_version.versionName == expected_new_version


# --- release candidate limit: superseded candidates make room (opt-in) ---------------------------


def _distribution(
    version_id, name, stage="DEV", create_date="2026-10-01T00:00:00+00:00", recommended=False, account="111111111111"
):
    return version.Version(
        projectId="project-123",
        productId="product-456",
        technologyId="tech-789",
        versionId=version_id,
        versionName=name,
        versionType="RELEASE_CANDIDATE",
        awsAccountId=account,
        stage=stage,
        region="eu-north-1",
        status=version.VersionStatus.Created,
        scPortfolioId="port-12345",
        isRecommendedVersion=recommended,
        createDate=create_date,
        lastUpdateDate=create_date,
        createdBy="T123456",
        lastUpdatedBy="T123456",
    )


def test_superseded_rc_versions_are_dev_only_unrecommended_candidates_oldest_first(mock_product_entity):
    # ARRANGE
    versions_qry_srv = Mock()
    versions_qry_srv.get_product_version_distributions.return_value = [
        _distribution("vers-new", "1.0.1-rc.1", create_date="2026-10-01T08:00:00+00:00"),
        _distribution("vers-old", "1.0.0-rc.1", create_date="2026-10-01T06:00:00+00:00"),
        _distribution("vers-old", "1.0.0-rc.1", create_date="2026-10-01T06:00:00+00:00", account="222222222222"),
        _distribution("vers-prod", "0.9.0", stage="PROD", create_date="2026-09-01T00:00:00+00:00"),
        _distribution("vers-qa", "0.9.1-rc.1", create_date="2026-09-02T00:00:00+00:00"),
        _distribution("vers-qa", "0.9.1-rc.1", stage="QA", create_date="2026-09-02T00:00:00+00:00"),
        _distribution("vers-rec", "0.9.2-rc.1", create_date="2026-09-03T00:00:00+00:00", recommended=True),
    ]

    # ACT
    candidates = create_automated_version_event_handler._superseded_rc_versions(versions_qry_srv, mock_product_entity)

    # ASSERT
    assert [c.versionId for c in candidates] == ["vers-old", "vers-new"]


@pytest.mark.parametrize("retire, expect_retired", [(True, ["vers-old"]), (False, [])])
@patch("app.publishing.domain.event_handlers.create_automated_version_event_handler.retire_version_command_handler")
@patch("app.publishing.domain.model.version.generate_version_id", return_value="vers-11111111")
def test_handle_at_the_rc_limit_retires_the_oldest_candidate_when_enabled(
    _mock_generate_version_id,
    mock_retire_handler,
    retire,
    expect_retired,
    mock_unit_of_work,
    mock_message_bus,
    mock_portfolios_qry_srv,
    mock_versions_qry_srv,
    mock_template_domain_qry_srv,
    mock_param_service,
    mock_stack_srv,
    mock_file_service,
    mock_product_entity,
    mock_logger,
):
    # ARRANGE: two active candidates, limit two
    mock_unit_of_work.get_repository.side_effect = lambda pk, entity: (
        Mock(get=Mock(return_value=mock_product_entity)) if entity == product.Product else Mock()
    )
    mock_versions_qry_srv.get_latest_version_name_and_id.return_value = ("1.0.1-rc.1", "vers-new")
    mock_versions_qry_srv.get_distinct_number_of_versions.side_effect = (
        lambda product_id, status=None, version_name_filter=None: 2
    )
    mock_versions_qry_srv.get_product_version_distributions.return_value = [
        _distribution("vers-new", "1.0.1-rc.1", create_date="2026-10-01T08:00:00+00:00"),
        _distribution("vers-old", "1.0.0-rc.1", create_date="2026-10-01T06:00:00+00:00"),
    ]
    mock_param_service.get_parameter_value.side_effect = lambda parameter_name: (
        "2" if "rc-limit" in parameter_name else "5"
    )
    mock_template_domain_qry_srv.get_latest_draft_template.return_value = "template"
    mock_stack_srv.validate_template.return_value = (True, [], None)
    mock_template_domain_qry_srv.get_default_template_file_name.return_value = "workbench-template.yml"

    # ACT
    def act():
        create_automated_version_event_handler.handle(
            ami_id="ami-12345678",
            product_id="product-456",
            project_id="project-123",
            release_type="MINOR",
            user_id="T123456",
            component_version_details=get_default_component_details(),
            os_version="Ubuntu 24.04",
            platform="Linux",
            architecture="x86_64",
            integrations=[],
            template_domain_qry_srv=mock_template_domain_qry_srv,
            logger=mock_logger,
            uow=mock_unit_of_work,
            message_bus=mock_message_bus,
            portf_qry_srv=mock_portfolios_qry_srv,
            version_qry_srv=mock_versions_qry_srv,
            param_service=mock_param_service,
            product_version_limit_param_name="version-limit",
            product_rc_version_limit_param_name="rc-limit",
            stack_srv=mock_stack_srv,
            file_service=mock_file_service,
            retire_superseded_rc_versions=retire,
        )

    # ASSERT
    if retire:
        act()
        mock_message_bus.publish.assert_called()
    else:
        with pytest.raises(domain_exception.DomainException, match="maximum number of active RC versions"):
            act()
    retired = [c.kwargs["command"].versionId.value for c in mock_retire_handler.handle.call_args_list]
    assert retired == expect_retired
