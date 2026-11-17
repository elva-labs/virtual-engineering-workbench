"""the authorization sync reads every page of programs and assignments."""

import assertpy

from app.authorization.adapters.query_services import projects_api_query_service
from app.shared.adapters.boto import paging_utils
from app.shared.test_utils.paged_projects_api import PagedProjectsApi


def test_get_projects_reads_300_programs_and_sends_each_token_as_json():
    # The route answers the token as an object; sent as is, requests split it and page 2 failed.
    api = PagedProjectsApi(projects=300)
    srv = projects_api_query_service.ProjectsApiQueryService(api=api)

    project_ids, token = [], None
    while True:
        page = srv.get_projects(page=paging_utils.PageInfo(page_size=100, page_token=token))
        project_ids += [p.projectId for p in page.items]
        token = page.page_token
        if not token:
            break

    assertpy.assert_that(project_ids).is_length(300)
    assertpy.assert_that(api.calls_to("internal/projects")[1]["nextToken"]).is_equal_to(
        '{"PK":"PAGE","SK":"OFFSET#100"}'
    )


def test_get_user_assignments_reads_300_assignments():
    api = PagedProjectsApi(user_assignments=300)
    srv = projects_api_query_service.ProjectsApiQueryService(api=api)

    assertpy.assert_that(srv.get_user_assignments("USER-1")).is_length(300)
    assertpy.assert_that(api.calls_to("internal/user/assignments")).is_length(3)
