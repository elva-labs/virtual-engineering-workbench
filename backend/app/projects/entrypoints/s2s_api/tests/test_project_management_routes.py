import json

from app.projects.entrypoints.s2s_api.tests.test_management_routes import invoke, make_dependencies

BODY = {"managedBy": "terraform", "source": "example-org/config programs/example"}


def test_get_is_not_found_while_the_portal_owns_the_project(lambda_context, authenticated_event):
    deps, _, _, _, _ = make_dependencies()
    response = invoke(deps, authenticated_event, lambda_context, "GET", "/projects/project-id/management")
    assert response["statusCode"] == 404


def test_put_marks_the_project_and_get_reads_it(lambda_context, authenticated_event):
    deps, query, lifecycle, _, _ = make_dependencies()
    response = invoke(deps, authenticated_event, lambda_context, "PUT", "/projects/project-id/management", BODY)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"projectId": "project-id", **BODY}
    lifecycle.set_management.assert_called_once_with("project-id", "terraform", BODY["source"])

    query.get_project_by_id.return_value = query.get_project_by_id.return_value.model_copy(
        update={"managedBy": "terraform", "managedSource": BODY["source"]}
    )
    response = invoke(deps, authenticated_event, lambda_context, "GET", "/projects/project-id/management")
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["managedBy"] == "terraform"


def test_put_rejects_an_invalid_tool_name(lambda_context, authenticated_event):
    deps, _, lifecycle, _, _ = make_dependencies()
    response = invoke(
        deps,
        authenticated_event,
        lambda_context,
        "PUT",
        "/projects/project-id/management",
        {**BODY, "managedBy": "Bad Name"},
    )
    assert response["statusCode"] in (400, 422)
    lifecycle.set_management.assert_not_called()


def test_put_requires_the_client_assignment(lambda_context, authenticated_event):
    deps, _, lifecycle, _, _ = make_dependencies()
    response = invoke(deps, authenticated_event, lambda_context, "PUT", "/projects/other-project/management", BODY)
    assert response["statusCode"] == 403
    lifecycle.set_management.assert_not_called()


def test_delete_is_idempotent(lambda_context, authenticated_event):
    deps, query, lifecycle, _, _ = make_dependencies()
    response = invoke(deps, authenticated_event, lambda_context, "DELETE", "/projects/project-id/management")
    assert response["statusCode"] == 204
    lifecycle.set_management.assert_called_once_with("project-id", None, None)

    query.get_project_by_id.return_value = None
    response = invoke(deps, authenticated_event, lambda_context, "DELETE", "/projects/project-id/management")
    assert response["statusCode"] == 204


LIFECYCLE = {
    "alwaysOn": False,
    "idleStopMinutes": 90,
    "nightlyStop": True,
    "weekendStop": None,
    "allowUserDisableNightlyStop": True,
    "allowUserIdleTimeout": True,
    "userIdleTimeoutMinMinutes": 10,
    "userIdleTimeoutMaxMinutes": 240,
}


def test_workbench_lifecycle_get_is_not_found_while_on_the_defaults(lambda_context, authenticated_event):
    deps, _, _, _, _ = make_dependencies()
    response = invoke(deps, authenticated_event, lambda_context, "GET", "/projects/project-id/workbench-lifecycle")
    assert response["statusCode"] == 404


def test_workbench_lifecycle_put_get_and_delete(lambda_context, authenticated_event):
    from app.projects.domain.model import workbench_lifecycle

    deps, query, lifecycle, _, _ = make_dependencies()
    response = invoke(
        deps, authenticated_event, lambda_context, "PUT", "/projects/project-id/workbench-lifecycle", LIFECYCLE
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"projectId": "project-id", **LIFECYCLE}
    stored = workbench_lifecycle.WorkbenchLifecycle(**LIFECYCLE)
    lifecycle.set_workbench_lifecycle.assert_called_once_with("project-id", stored)

    query.get_project_by_id.return_value = query.get_project_by_id.return_value.model_copy(
        update={"workbenchLifecycle": stored}
    )
    response = invoke(deps, authenticated_event, lambda_context, "GET", "/projects/project-id/workbench-lifecycle")
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["idleStopMinutes"] == 90

    response = invoke(deps, authenticated_event, lambda_context, "DELETE", "/projects/project-id/workbench-lifecycle")
    assert response["statusCode"] == 204
    lifecycle.set_workbench_lifecycle.assert_called_with("project-id", None)


def test_workbench_lifecycle_rejects_bounds_in_the_wrong_order(lambda_context, authenticated_event):
    deps, _, lifecycle, _, _ = make_dependencies()
    body = {**LIFECYCLE, "userIdleTimeoutMinMinutes": 500, "userIdleTimeoutMaxMinutes": 60}
    response = invoke(deps, authenticated_event, lambda_context, "PUT", "/projects/project-id/workbench-lifecycle", body)
    assert response["statusCode"] in (400, 422)
    lifecycle.set_workbench_lifecycle.assert_not_called()
