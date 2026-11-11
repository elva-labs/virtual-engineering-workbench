"""Resolve effective assignments without materializing group members as users."""

from app.projects.domain.model import project_assignment
from app.shared.identity import oidc_groups


def effective_assignment(user_id: str, project_id: str, direct, group_assignments):
    roles = oidc_groups.effective_roles(
        [direct] if direct else [],
        [assignment for assignment in group_assignments if assignment.projectId == project_id],
    ).get(project_id, [])
    if not roles:
        return direct
    if direct:
        return direct.model_copy(update={"roles": roles})
    return project_assignment.Assignment(userId=user_id, projectId=project_id, roles=roles)
