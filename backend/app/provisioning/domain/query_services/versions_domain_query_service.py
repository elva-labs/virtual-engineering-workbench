import typing

from app.provisioning.domain.model import stage_access
from app.provisioning.domain.ports import versions_query_service
from app.provisioning.domain.read_models import version
from app.provisioning.domain.value_objects import (
    aws_account_id_value_object,
    product_id_value_object,
    region_value_object,
    version_id_value_object,
    version_stage_value_object,
)


class VersionsDomainQueryService:
    def __init__(self, version_qry_srv: versions_query_service.VersionsQueryService):
        self._version_qry_srv = version_qry_srv

    def get_versions_ready_for_provisioning(
        self,
        product_id: product_id_value_object.ProductIdValueObject,
        stage: version_stage_value_object.VersionStageValueObject | None,
        region: region_value_object.RegionValueObject | None,
        return_technical_params: bool = True,
        user_roles: list[str] | None = None,
        project_id: str | None = None,
    ) -> typing.List[version.Version]:
        versions = self._version_qry_srv.get_product_version_distributions(
            product_id=product_id.value,
            stage=stage.value if stage else None,
            region=region.value if region else None,
        )
        # Versions are keyed by product only: a product id from another project must return nothing.
        if project_id is not None:
            versions = [vers for vers in versions if vers.projectId == project_id]
        # A user sees only the stages their role may consume; service clients see all.
        if user_roles is not None:
            allowed = stage_access.allowed_stages(user_roles)
            versions = [vers for vers in versions if str(vers.stage) in allowed]

        if not return_technical_params:
            for vers in versions:
                vers.parameters = [param for param in vers.parameters if not param.isTechnicalParameter]

        return versions

    def get_version_distribution(
        self,
        product_id: product_id_value_object.ProductIdValueObject,
        version_id: version_id_value_object.VersionIdValueObject,
        aws_account_id: aws_account_id_value_object.AWSAccountIDValueObject,
        stage: version_stage_value_object.VersionStageValueObject,
    ) -> version.Version | None:
        version_distribution = self._version_qry_srv.get_product_version_distribution(
            product_id=product_id.value,
            version_id=version_id.value,
            aws_account_id=aws_account_id.value,
            stage=stage.value,
        )

        return version_distribution
