"""Retiring a product version, for service clients.

An externally managed project refuses portal writes, so its Terraform declares a retirement as a
resource (projectId/productId/versionId). PUT is an upsert: it starts the retirement once (VEW
unpublishes the version in every account) and a repeat returns the state - 202 while distributions
are RETIRING, 200 once all are RETIRED. GET reads it for import and answers 404 while the version is
not retired. DELETE only forgets it: VEW restores a retired version to DEV only through the portal, a
deliberate step.

Guard rails besides the domain's (not the latest version; every distribution CREATED; PROD needs the
project owner's role, which the client acts with): a version that workbenches are still on is refused
with 409 unless `force` is set, because those workbenches can no longer change size until they update.
"""

from http import HTTPStatus

from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import api_gateway, content_types

from app.publishing.domain.commands import retire_version_command
from app.publishing.domain.exceptions import s2s_exception
from app.publishing.domain.model import version
from app.publishing.domain.value_objects import (
    product_id_value_object,
    project_id_value_object,
    user_id_value_object,
    user_role_value_object,
    version_id_value_object,
)
from app.publishing.entrypoints.s2s_api import bootstrapper, common
from app.publishing.entrypoints.s2s_api.model import api_model
from app.shared.middleware.authorization import VirtualWorkbenchRoles

tracer = Tracer()

READ_SCOPE = "clients/publishing/version.read"
RETIRE_SCOPE = "clients/publishing/version.retire"

# The portal's rule for retiring a PROD version (retire_version_command_handler): power user and above.
# The client's project assignment and the version.retire scope are the gate; the reviewed PR is the
# decision.
SERVICE_ROLE = VirtualWorkbenchRoles.ProgramOwner.value

RETIRED_STATES = {version.VersionStatus.Retiring.value, version.VersionStatus.Retired.value}


class VersionInUse(s2s_exception.ResourceConflict):
    code = "VERSION_IN_USE"
    retryable = False

    def __init__(self, count: int):
        self.detail = (
            f"{count} workbench(es) are still on this version: retired, they keep running but can no longer"
            " change size until their owners update. Update or remove them first, or set force."
        )
        super().__init__(self.detail)


def _status(distributions: list[version.Version]) -> str:
    statuses = {str(d.status.value) for d in distributions}
    if statuses == {version.VersionStatus.Retired.value}:
        return version.VersionStatus.Retired.value
    if version.VersionStatus.Retiring.value in statuses or version.VersionStatus.Retired.value in statuses:
        return version.VersionStatus.Retiring.value
    return next(iter(sorted(statuses)))


def _retirement(project_id: str, distributions: list[version.Version]) -> api_model.VersionRetirement:
    first = distributions[0]
    return api_model.VersionRetirement(
        projectId=project_id,
        productId=first.productId,
        versionId=first.versionId,
        versionName=first.versionName,
        status=_status(distributions),
        retireReason=next((d.retireReason for d in distributions if d.retireReason), None),
        distributions=[
            api_model.VersionDistribution(awsAccountId=d.awsAccountId, region=d.region, status=d.status.value)
            for d in distributions
        ],
    )


def _json(status: HTTPStatus, body, retry: bool = False) -> api_gateway.Response:
    return api_gateway.Response(
        status_code=int(status),
        body=body,
        headers={**common.NO_STORE, **({"Retry-After": "30"} if retry else {})},
        content_type=content_types.APPLICATION_JSON,
    )


def request_of(router: api_gateway.Router) -> api_model.RetireVersionRequest:
    # The body is optional: a bare PUT retires with the default reason and without force.
    body = router.current_event.body
    try:
        return api_model.RetireVersionRequest.model_validate_json(body) if body else api_model.RetireVersionRequest()
    except ValueError as error:
        raise s2s_exception.InvalidRequest() from error


def init(dependencies: bootstrapper.Dependencies) -> api_gateway.Router:  # noqa: C901
    router = api_gateway.Router()

    def distributions_in_project(project_id: str, product_id: str, version_id: str) -> list[version.Version]:
        if dependencies.products_query_service.get_product(project_id, product_id) is None:
            raise s2s_exception.ResourceNotFound()
        distributions = [
            d
            for d in dependencies.versions_query_service.get_product_version_distributions(
                product_id=product_id, version_id=version_id
            )
            if d.projectId == project_id
        ]
        if not distributions:
            raise s2s_exception.ResourceNotFound()
        return distributions

    def is_retired(distributions: list[version.Version]) -> bool:
        return any(str(d.status.value) in RETIRED_STATES for d in distributions)

    def response_for(project_id: str, distributions: list[version.Version]) -> api_gateway.Response:
        retirement = _retirement(project_id, distributions)
        done = retirement.status == version.VersionStatus.Retired.value
        return _json(HTTPStatus.OK if done else HTTPStatus.ACCEPTED, retirement, retry=not done)

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.get("/projects/<project_id>/products/<product_id>/versions/<version_id>/retirement")
    def get_retirement(project_id: str, product_id: str, version_id: str):
        common.authorize(router, dependencies, project_id, READ_SCOPE)
        distributions = distributions_in_project(project_id, product_id, version_id)
        if not is_retired(distributions):
            raise s2s_exception.ResourceNotFound()
        return response_for(project_id, distributions)

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.put("/projects/<project_id>/products/<product_id>/versions/<version_id>/retirement")
    def retire(project_id: str, product_id: str, version_id: str):
        client = common.authorize(router, dependencies, project_id, RETIRE_SCOPE)
        request = request_of(router)
        distributions = distributions_in_project(project_id, product_id, version_id)
        if is_retired(distributions):
            # Already retired (or being retired): the upsert returns the current state.
            return response_for(project_id, distributions)

        if not request.force:
            workbenches = dependencies.version_workbenches_query_service
            if workbenches is None:
                # Unknown is not none: never retire a version we could not check.
                raise s2s_exception.ResourceReadNotReady()
            in_use = workbenches.get_active_workbench_ids(product_id, version_id)
            if in_use:
                raise VersionInUse(len(in_use))

        # Validation (latest version, every distribution CREATED, PROD role) is the domain's: 422.
        dependencies.command_bus.handle(
            retire_version_command.RetireVersionCommand(
                projectId=project_id_value_object.from_str(project_id),
                productId=product_id_value_object.from_str(product_id),
                versionId=version_id_value_object.from_str(version_id),
                retiredBy=user_id_value_object.from_str(f"service:{client}"),
                userRoles=[user_role_value_object.from_str(SERVICE_ROLE)],
                retireReason=request.reason or "Retired through Terraform",
            )
        )
        retired = distributions_in_project(project_id, product_id, version_id)
        if not is_retired(retired):
            raise s2s_exception.ResourceReadNotReady()
        return response_for(project_id, retired)

    @tracer.capture_method(capture_response=False, capture_error=False)
    @router.delete("/projects/<project_id>/products/<product_id>/versions/<version_id>/retirement")
    def forget_retirement(project_id: str, product_id: str, version_id: str):
        common.authorize(router, dependencies, project_id, RETIRE_SCOPE)
        return api_gateway.Response(status_code=HTTPStatus.NO_CONTENT, headers=common.NO_STORE)

    return router
