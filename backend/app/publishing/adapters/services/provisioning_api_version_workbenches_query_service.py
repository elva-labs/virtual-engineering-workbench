from urllib.parse import quote

from app.publishing.domain.exceptions.s2s_exception import ResourceReadNotReady
from app.publishing.domain.ports.version_workbenches_query_service import VersionWorkbenchesQueryService
from app.shared.api import aws_api


class ProvisioningApiVersionWorkbenchesQueryService(VersionWorkbenchesQueryService):
    """Asks Provisioning's internal API which workbenches are on a version."""

    def __init__(self, api: aws_api.AWSAPIBase) -> None:
        self._aws_api = api

    def get_active_workbench_ids(self, product_id: str, version_id: str) -> list[str]:
        path = "/".join(
            ["internal", "products", quote(product_id, safe=""), "versions", quote(version_id, safe=""), "provisioned"]
        )
        try:
            response = self._aws_api.call_api(path=path, http_method="GET")
        except Exception as exc:
            # Unknown is not none: never retire a version we could not check.
            raise ResourceReadNotReady() from exc
        items = response.get("provisionedProducts") if isinstance(response, dict) else None
        if not isinstance(items, list):
            raise ResourceReadNotReady()
        return [str(item.get("provisionedProductId")) for item in items if isinstance(item, dict)]
