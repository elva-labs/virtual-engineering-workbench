from unittest import mock

import pytest
import requests

from app.publishing.adapters.query_services.projects_api_technologies_query_service import (
    ProjectsApiTechnologiesQueryService,
)
from app.publishing.domain.exceptions.s2s_exception import ResourceReadNotReady
from app.shared.api import aws_api


@pytest.fixture()
def api():
    return mock.create_autospec(spec=aws_api.AWSAPIBase)


def http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


def test_get_technology_reads_the_projects_internal_route(api):
    api.call_api.return_value = {"technology": {"id": "tech-1", "name": "Ubuntu", "projectId": "proj/one"}}

    found = ProjectsApiTechnologiesQueryService(api=api).get_technology("proj/one", "tech-1")

    assert found.technologyId == "tech-1" and found.technologyName == "Ubuntu"
    api.call_api.assert_called_once_with(path="internal/projects/proj%2Fone/technologies/tech-1", http_method="GET")


def test_get_technology_returns_none_when_missing(api):
    api.call_api.side_effect = http_error(404)

    assert ProjectsApiTechnologiesQueryService(api=api).get_technology("proj-1", "tech-1") is None


@pytest.mark.parametrize("failure", [http_error(500), RuntimeError("boom")])
def test_get_technology_failures_are_retryable(api, failure):
    api.call_api.side_effect = failure

    with pytest.raises(ResourceReadNotReady):
        ProjectsApiTechnologiesQueryService(api=api).get_technology("proj-1", "tech-1")


def test_get_technology_rejects_malformed_response(api):
    api.call_api.return_value = {"technology": {"id": "tech-1"}}

    with pytest.raises(ResourceReadNotReady):
        ProjectsApiTechnologiesQueryService(api=api).get_technology("proj-1", "tech-1")
