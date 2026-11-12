"""the internal list routes take back the token they answered, so a caller reads all
300 programs. Before, they passed the nextToken string to DynamoDB and page 2 failed."""

import json
from unittest import mock

import assertpy
import pytest
from aws_lambda_powertools import logging

from app.projects.domain.model import project, project_assignment
from app.projects.domain.ports import user_directory_service
from app.projects.entrypoints.api import bootstrapper
from app.projects.entrypoints.api.tests import fake_classes
from app.shared.adapters.message_bus import in_memory_command_bus
from app.shared.api import paging

PROGRAMS = 300


class PagedProjectsQueryService(fake_classes.FakeProjectsQueryService):
    """Pages like DynamoDB: a dict token, and refuses anything else as ExclusiveStartKey."""

    def __init__(self):
        self.projects = [project.Project(projectId=f"proj-{i:04d}", projectName=f"P{i}", isActive=True) for i in range(PROGRAMS)]
        self.tokens: list = []

    def _page(self, items, page_size, next_token):
        self.tokens.append(next_token)
        if next_token is not None and not isinstance(next_token, dict):
            raise TypeError("ExclusiveStartKey must be a map")
        start = next_token["offset"] if next_token else 0
        end = start + page_size
        return items[start:end], ({"offset": end} if end < len(items) else None)

    def list_projects(self, page_size, next_token, user_id=None):
        items, token = self._page(self.projects, page_size, next_token)
        return items, token, []

    def list_projects_by_user(self, user_id, page_size, next_token):
        items, token = self._page(self.projects, page_size, next_token)
        assignments = [project_assignment.Assignment(userId=user_id, projectId=p.projectId, roles=[]) for p in items]
        return items, token, assignments


@pytest.fixture
def paged_dependencies():
    directory = mock.create_autospec(spec=user_directory_service.UserDirectoryService, instance=True)
    directory.get_user_profiles.return_value = {}
    return bootstrapper.Dependencies(
        projects_query_service=PagedProjectsQueryService(),
        enrolment_query_service=fake_classes.FakeEnrolmentsQueryService(),
        technologies_query_service=fake_classes.FakeTechnologiesQueryService(),
        command_bus=in_memory_command_bus.InMemoryCommandBus(logger=mock.create_autospec(spec=logging.Logger)),
        user_directory_service=directory,
    )


def _read_all(handler, lambda_context, authenticated_event, path, params, key):
    items, token, pages = [], None, 0
    while True:
        query = {**params, **({"nextToken": paging.encode_page_token(token)} if token else {})}
        result = handler.handler(authenticated_event(None, path, "GET", query), lambda_context)
        assertpy.assert_that(result["statusCode"]).is_equal_to(200)
        body = json.loads(result["body"])
        items += body[key]
        pages += 1
        token = body.get("nextToken")
        if not token:
            return items, pages


def test_internal_projects_pages_through_300_programs(lambda_context, authenticated_event, paged_dependencies):
    from app.projects.entrypoints.api import handler

    handler.dependencies = paged_dependencies

    projects, pages = _read_all(
        handler, lambda_context, authenticated_event, "/internal/projects", {"pageSize": "100"}, "projects"
    )

    assertpy.assert_that([p["projectId"] for p in projects]).is_equal_to([f"proj-{i:04d}" for i in range(PROGRAMS)])
    assertpy.assert_that(pages).is_equal_to(3)
    assertpy.assert_that(paged_dependencies.projects_query_service.tokens).is_equal_to(
        [None, {"offset": 100}, {"offset": 200}]
    )


def test_internal_user_assignments_pages_through_300(lambda_context, authenticated_event, paged_dependencies):
    from app.projects.entrypoints.api import handler

    handler.dependencies = paged_dependencies

    assignments, pages = _read_all(
        handler,
        lambda_context,
        authenticated_event,
        "/internal/user/assignments",
        {"pageSize": "100", "userId": "USER-1"},
        "assignments",
    )

    assertpy.assert_that(assignments).is_length(PROGRAMS)
    assertpy.assert_that(pages).is_equal_to(3)


def test_internal_projects_refuses_a_token_that_is_not_one(lambda_context, authenticated_event, paged_dependencies):
    from app.projects.entrypoints.api import handler

    handler.dependencies = paged_dependencies

    result = handler.handler(
        authenticated_event(None, "/internal/projects", "GET", {"pageSize": "100", "nextToken": "PK"}), lambda_context
    )

    assertpy.assert_that(result["statusCode"]).is_equal_to(400)
