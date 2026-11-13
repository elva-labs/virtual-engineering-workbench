"""every list from the projects API is read past its first page, at 300 programs."""

import assertpy

from app.provisioning.adapters.exceptions import adapter_exception
from app.provisioning.adapters.query_services import projects_api_query_service
from app.shared.test_utils.paged_projects_api import PagedProjectsApi


def _srv(api: PagedProjectsApi) -> projects_api_query_service.ProjectsApiQueryService:
    return projects_api_query_service.ProjectsApiQueryService(api=api)


def test_get_projects_reads_every_page_of_300_programs():
    api = PagedProjectsApi(projects=300)

    projects = _srv(api).get_projects()

    assertpy.assert_that([p.projectId for p in projects]).is_equal_to([f"proj-{i:04d}" for i in range(300)])
    assertpy.assert_that(api.calls_to("internal/projects")).is_length(3)
    assertpy.assert_that(api.calls_to("internal/projects")[0]).is_equal_to({"pageSize": "100"})


def test_get_projects_sends_the_token_back_as_json():
    api = PagedProjectsApi(projects=101)

    projects = _srv(api).get_projects()

    assertpy.assert_that(projects).is_length(101)
    assertpy.assert_that(api.calls_to("internal/projects")[1]["nextToken"]).is_equal_to(
        '{"PK":"PAGE","SK":"OFFSET#100"}'
    )


def test_get_project_finds_a_program_past_100_and_stops_at_its_page():
    api = PagedProjectsApi(projects=300)

    found = _srv(api).get_project("proj-0150")

    assertpy.assert_that(found.projectName).is_equal_to("Program 150")
    assertpy.assert_that(api.calls_to("internal/projects")).is_length(2)


def test_get_project_raises_for_an_unknown_program_after_every_page():
    api = PagedProjectsApi(projects=300)

    assertpy.assert_that(_srv(api).get_project).raises(adapter_exception.AdapterException).when_called_with(
        "proj-9999"
    )
    assertpy.assert_that(api.calls_to("internal/projects")).is_length(3)


def test_get_aws_accounts_by_status_reads_every_page():
    api = PagedProjectsApi(projects=1, accounts_per_project=300)

    accounts = _srv(api).get_aws_accounts_by_status(project_id="proj-0000", statuses=["Active"])

    assertpy.assert_that(accounts).is_length(150)
    assertpy.assert_that(api.calls_to("internal/accounts")).is_length(3)


def test_get_aws_account_by_id_finds_an_account_past_page_one():
    api = PagedProjectsApi(projects=1, accounts_per_project=300)

    account = _srv(api).get_aws_account_by_id("proj-0000", "proj-0000-acct-0299")

    assertpy.assert_that(account.id).is_equal_to("proj-0000-acct-0299")


def test_get_user_assignments_count_counts_every_page():
    api = PagedProjectsApi(user_assignments=300)

    assertpy.assert_that(_srv(api).get_user_assignments_count("user-1")).is_equal_to(300)
    assertpy.assert_that(api.calls_to("internal/user/assignments")).is_length(3)


def test_a_repeated_page_token_stops_the_read():
    class Looping(PagedProjectsApi):
        def call_api(self, path, http_method, query_params=None, **_):
            self.calls.append((path, dict(query_params or {})))
            return {"projects": [], "nextToken": {"PK": "PAGE", "SK": "OFFSET#1"}}

    assertpy.assert_that(_srv(Looping()).get_projects).raises(RuntimeError).when_called_with()
