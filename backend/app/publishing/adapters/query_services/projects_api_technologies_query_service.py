import typing
from urllib.parse import quote

import requests

from app.publishing.domain.exceptions.s2s_exception import ResourceReadNotReady
from app.publishing.domain.ports.technologies_query_service import TechnologiesQueryService
from app.publishing.domain.read_models import technology
from app.shared.api import aws_api


class ProjectsApiTechnologiesQueryService(TechnologiesQueryService):
    """Reads a project's technology from the Projects internal API; a missing technology is None."""

    def __init__(self, api: aws_api.AWSAPIBase) -> None:
        self._aws_api = api

    def get_technology(self, project_id: str, technology_id: str) -> typing.Optional[technology.Technology]:
        path = "/".join(
            ["internal", "projects", quote(project_id, safe=""), "technologies", quote(technology_id, safe="")]
        )
        try:
            response = self._aws_api.call_api(path=path, http_method="GET")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise ResourceReadNotReady() from exc
        except Exception as exc:
            raise ResourceReadNotReady() from exc
        try:
            found = response["technology"]
            return technology.Technology(technologyId=found["id"], technologyName=found["name"])
        except (KeyError, TypeError) as exc:
            raise ResourceReadNotReady() from exc
