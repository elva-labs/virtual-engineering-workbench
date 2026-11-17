"""GET /projects on the S2S API takes back the nextToken it answered (as JSON), so a client reads
all 300 programs. Before, the token went to DynamoDB as a string and page 2 failed."""

import json

from app.projects.domain.model import project
from app.projects.entrypoints.s2s_api.tests.test_management_routes import invoke, make_dependencies
from app.shared.api import paging

PROGRAMS = 300


def _paged(page_size, next_token, user_id=None):
    if next_token is not None and not isinstance(next_token, dict):
        raise TypeError("ExclusiveStartKey must be a map")
    start = next_token["offset"] if next_token else 0
    end = start + page_size
    items = [
        project.Project(projectId=f"proj-{i:04d}", projectName=f"P{i}", isActive=True)
        for i in range(start, min(end, PROGRAMS))
    ]
    return items, ({"offset": end} if end < PROGRAMS else None), []


def _with_query(authenticated_event, query):
    def event(body, path, method, query_params=None):
        built = authenticated_event(body, path, method, query_params)
        built["queryStringParameters"] = query
        return built

    return event


def test_list_projects_pages_through_300_programs(lambda_context, authenticated_event):
    deps, query, _, _, _ = make_dependencies()
    query.list_projects.side_effect = _paged

    ids, token, pages = [], None, 0
    while True:
        params = {"pageSize": "100", **({"nextToken": paging.encode_page_token(token)} if token else {})}
        response = invoke(deps, _with_query(authenticated_event, params), lambda_context, "GET", "/projects")
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        ids += [p["projectId"] for p in body["projects"]]
        pages += 1
        token = body.get("nextToken")
        if not token:
            break

    assert ids == [f"proj-{i:04d}" for i in range(PROGRAMS)]
    assert pages == 3


def test_list_projects_refuses_a_token_that_is_not_one(lambda_context, authenticated_event):
    deps, query, _, _, _ = make_dependencies()

    response = invoke(
        deps,
        _with_query(authenticated_event, {"pageSize": "100", "nextToken": "PK"}),
        lambda_context,
        "GET",
        "/projects",
    )

    assert response["statusCode"] == 400
    query.list_projects.assert_not_called()
