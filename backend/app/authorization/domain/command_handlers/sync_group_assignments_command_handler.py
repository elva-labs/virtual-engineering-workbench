from app.authorization.domain.integration_events.projects.project_group_assignment_changed import (
    ProjectGroupAssignmentChanged,
)
from app.authorization.domain.integration_event_handlers.projects import (
    project_group_assignment_changed_handler,
)
from app.shared.adapters.boto import paging_utils


def handle(projects_qs, uow):
    page_token = None
    while True:
        page = projects_qs.get_projects(
            paging_utils.PageInfo(page_size=100, page_token=page_token)
        )
        for project in page.items:
            for assignment in projects_qs.get_project_group_assignments(
                project.projectId
            ):
                project_group_assignment_changed_handler.handle(
                    ProjectGroupAssignmentChanged(**assignment.model_dump()), uow
                )
        if not page.page_token:
            return
        page_token = page.page_token
