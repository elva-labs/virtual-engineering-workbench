"""The program experience on the S2S project routes: workbench-only programs (members launch
workbenches only, from PROD releases). Given on create and update, omitted keeps the value."""

from uuid import uuid4

from app.projects.domain.events.projects import project_updated
from app.projects.domain.model import project
from app.projects.entrypoints.s2s_api.tests.test_management_routes import invoke, make_dependencies


def test_create_passes_the_experience(lambda_context, authenticated_event):
    deps, _, lifecycle, _, _ = make_dependencies()
    key = str(uuid4())

    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "POST",
        "/projects",
        {"name": "Shared", "description": None, "isActive": True, "experience": "workbench-only"},
        key,
    )

    assert response["statusCode"] == 201
    lifecycle.create.assert_called_once_with("fake_client_id", key, "Shared", None, True, None, "workbench-only")


def test_update_passes_the_experience_and_omitted_is_none(lambda_context, authenticated_event):
    deps, _, lifecycle, _, _ = make_dependencies()

    invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        "/projects/project-id",
        {"name": "New", "description": None, "isActive": True, "experience": "full"},
    )
    invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        "/projects/project-id",
        {"name": "New", "description": None, "isActive": True},
    )

    first, second = lifecycle.update.call_args_list
    assert first.args[-1] == "full"
    assert second.args[-1] is None


def test_new_projects_are_full_and_project_updated_carries_the_experience():
    assert project.Project(projectName="p", isActive=True).experience == project.EXPERIENCE_FULL

    created = project.Project(projectId="proj-1", projectName="p", isActive=True, experience="workbench-only")
    event = project_updated.from_project(created)

    assert event.model_dump(by_alias=True)["experience"] == "workbench-only"
