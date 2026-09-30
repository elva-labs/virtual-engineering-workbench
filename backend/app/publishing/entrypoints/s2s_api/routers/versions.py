"""Product versions and their promotion to a stage, for service clients.

A client (for example an infrastructure-as-code tool) tests a DEV version and promotes it: the
promotion is a resource (projectId/productId/versionId/stage). PUT is an upsert - promoting an
already promoted version returns its state - and GET reads it for import. DELETE only forgets it: a
release is not undone by removing the resource (retiring a version is a separate, deliberate step).
"""

from collections import OrderedDict
from http import HTTPStatus

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types

from app.publishing.domain.commands import promote_version_command
from app.publishing.domain.exceptions import s2s_exception
from app.publishing.domain.model import version
from app.publishing.domain.value_objects import (
    product_id_value_object,
    project_id_value_object,
    stage_value_object,
    user_id_value_object,
    user_role_value_object,
    version_id_value_object,
)
from app.publishing.entrypoints.s2s_api import bootstrapper, common
from app.publishing.entrypoints.s2s_api.model import api_model
from app.shared.middleware.authorization import VirtualWorkbenchRoles

tracer = Tracer()

READ_SCOPE = "clients/publishing/version.read"
PROMOTE_SCOPE = "clients/publishing/version.promote"

# The service client promotes on the project's behalf with the role the portal requires for PROD
# promotions (promote_version_command_handler). Its project assignment and the version.promote scope
# are the gate.
SERVICE_ROLE = VirtualWorkbenchRoles.ProgramOwner.value


def _stage_status(distributions: list[version.Version]) -> str:
    statuses = {str(d.status.value) for d in distributions}
    if statuses == {version.VersionStatus.Created.value}:
        return version.VersionStatus.Created.value
    if version.VersionStatus.Failed.value in statuses:
        return version.VersionStatus.Failed.value
    if statuses == {version.VersionStatus.Retired.value}:
        return version.VersionStatus.Retired.value
    return next(s for s in sorted(statuses) if s != version.VersionStatus.Created.value)


def _promotion(project_id: str, distributions: list[version.Version], stage: str) -> api_model.VersionPromotion:
    first = distributions[0]
    return api_model.VersionPromotion(
        projectId=project_id,
        productId=first.productId,
        versionId=first.versionId,
        versionName=first.versionName,
        stage=stage,
        status=_stage_status(distributions),
        distributions=[
            api_model.VersionDistribution(awsAccountId=d.awsAccountId, region=d.region, status=d.status.value)
            for d in distributions
        ],
    )


def _json(status: HTTPStatus, body, headers: dict | None = None) -> api_gateway.Response:
    return api_gateway.Response(
        status_code=int(status),
        body=body,
        headers={**common.NO_STORE, **(headers or {})},
        content_type=content_types.APPLICATION_JSON,
    )


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()

    def distributions_in_project(project_id: str, product_id: str, version_id: str | None = None):
        if dependencies.products_query_service.get_product(project_id, product_id) is None:
            raise s2s_exception.ResourceNotFound()
        return [
            d
            for d in dependencies.versions_query_service.get_product_version_distributions(
                product_id=product_id, version_id=version_id
            )
            if d.projectId == project_id
        ]

    def stage_of(value: str) -> str:
        try:
            return stage_value_object.from_str(value).value
        except Exception as error:
            raise s2s_exception.InvalidRequest() from error

    def in_stage(distributions: list[version.Version], stage: str) -> list[version.Version]:
        return [d for d in distributions if str(d.stage.value) == stage]

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/products/<product_id>/versions")
    def list_versions(project_id: str, product_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        grouped: "OrderedDict[str, list[version.Version]]" = OrderedDict()
        for d in sorted(distributions_in_project(project_id, product_id), key=lambda d: d.createDate):
            grouped.setdefault(d.versionId, []).append(d)
        versions = []
        for version_id, distributions in grouped.items():
            stages = sorted({str(d.stage.value) for d in distributions}, key=["DEV", "QA", "PROD"].index)
            versions.append(
                api_model.ProductVersion(
                    versionId=version_id,
                    versionName=distributions[0].versionName,
                    versionType=distributions[0].versionType,
                    stages=[
                        api_model.VersionStageState(stage=s, status=_stage_status(in_stage(distributions, s)))
                        for s in stages
                    ],
                )
            )
        return _json(HTTPStatus.OK, api_model.ProductVersionPage(versions=versions))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/products/<product_id>/versions/<version_id>/stages/<stage>")
    def get_promotion(project_id: str, product_id: str, version_id: str, stage: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        stage = stage_of(stage)
        distributions = in_stage(distributions_in_project(project_id, product_id, version_id), stage)
        if not distributions:
            raise s2s_exception.ResourceNotFound()
        return _json(HTTPStatus.OK, _promotion(project_id, distributions, stage))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put("/projects/<project_id>/products/<product_id>/versions/<version_id>/stages/<stage>")
    def promote(project_id: str, product_id: str, version_id: str, stage: str):
        client = common.authorize(router, dependencies, project_id, PROMOTE_SCOPE)
        stage = stage_of(stage)
        distributions = distributions_in_project(project_id, product_id, version_id)
        if not distributions:
            raise s2s_exception.ResourceNotFound()

        existing = in_stage(distributions, stage)
        if existing:
            # Already promoted (or being promoted): the upsert returns the current state.
            status = HTTPStatus.OK if _stage_status(existing) == version.VersionStatus.Created.value else None
            return _json(status or HTTPStatus.ACCEPTED, _promotion(project_id, existing, stage), _retry(status))

        # Validation (status, release candidate for PROD, target portfolios) is the domain's: 422.
        dependencies.command_bus.handle(
            promote_version_command.PromoteVersionCommand(
                projectId=project_id_value_object.from_str(project_id),
                productId=product_id_value_object.from_str(product_id),
                versionId=version_id_value_object.from_str(version_id),
                createdBy=user_id_value_object.from_str(f"service:{client}"),
                userRoles=[user_role_value_object.from_str(SERVICE_ROLE)],
                stage=stage_value_object.from_str(stage),
            )
        )
        promoted = in_stage(distributions_in_project(project_id, product_id, version_id), stage)
        if not promoted:
            raise s2s_exception.ResourceReadNotReady()
        return _json(HTTPStatus.ACCEPTED, _promotion(project_id, promoted, stage), _retry(None))

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.delete("/projects/<project_id>/products/<product_id>/versions/<version_id>/stages/<stage>")
    def forget_promotion(project_id: str, product_id: str, version_id: str, stage: str):
        common.authorize(router, dependencies, project_id, PROMOTE_SCOPE)
        stage_of(stage)
        return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT, headers=common.NO_STORE)

    return router


def _retry(status: HTTPStatus | None) -> dict:
    return {} if status == HTTPStatus.OK else {"Retry-After": "30"}
