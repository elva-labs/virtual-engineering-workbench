from app.projects.domain.command_handlers.users.reassign_user_command_handler import handle_reassign_user_command
from app.projects.domain.commands.users import assign_user_command as command
from app.projects.domain.commands.users.reassign_user_command import ReAssignUserCommand
from app.projects.domain.events.users import user_assigned
from app.projects.domain.exceptions import domain_exception
from app.projects.domain.model import project_assignment, user
from app.projects.domain.ports import projects_query_service, user_directory_service
from app.projects.domain.value_objects import user_id_value_object
from app.shared.adapters.message_bus import message_bus
from app.shared.adapters.unit_of_work_v2 import unit_of_work
from app.shared.identity import oidc_groups


def handle_assign_user_command(
    cmd: command.AssignUserCommand,
    unit_of_work: unit_of_work.UnitOfWork,
    projects_query_service: projects_query_service.ProjectsQueryService,
    message_bus: message_bus.MessageBus,
    user_directory_service: user_directory_service.UserDirectoryService,
):
    project = projects_query_service.get_project_by_id(cmd.project_id.value)
    if not project:
        raise domain_exception.DomainException(
            f"Failed to load project. Project for given ID {cmd.project_id.value} does not exist."
        )

    to_be_assigned_user_id = cmd.user_id.value.upper()

    to_be_assigned_user_assignment = projects_query_service.get_user_assignment(
        project_id=cmd.project_id.value, user_id=to_be_assigned_user_id
    )
    if to_be_assigned_user_assignment:
        if oidc_groups.provider_name() and to_be_assigned_user_assignment.source == "GROUP" and not cmd.group_ids:
            handle_reassign_user_command(
                ReAssignUserCommand(
                    project_id=cmd.project_id,
                    user_ids=[cmd.user_id],
                    initiating_user_id=user_id_value_object.from_str(
                        "membership", user_id_value_object.UserIdType.Service
                    ),
                    roles=cmd.roles,
                ),
                unit_of_work,
                projects_query_service,
                message_bus,
            )
            return
        raise domain_exception.DomainException(f"User with User ID {to_be_assigned_user_id} already exists.")

    assigned_roles = [role.value for role in cmd.roles]

    # Look up the user's email in the identity provider so the Members UI
    # (and downstream notifications) can display it. Best-effort: a missing
    # email does not block onboarding — the assignment still persists with
    # userEmail=None, matching the pre-existing fallback behavior.
    user_email = user_directory_service.get_user_email(to_be_assigned_user_id)

    assignment = project_assignment.Assignment(
        userId=to_be_assigned_user_id,
        projectId=cmd.project_id.value,
        roles=assigned_roles,
        source="GROUP" if cmd.group_ids else "DIRECT",
        groupIds=cmd.group_ids,
        trustedGroupIds=cmd.trusted_group_ids,
        userEmail=user_email,
        activeDirectoryGroups=[],
        activeDirectoryGroupStatus=user.UserADStatus.PENDING,
    )

    with unit_of_work:
        unit_of_work.get_repository(project_assignment.AssignmentPrimaryKey, project_assignment.Assignment).add(
            assignment
        )
        unit_of_work.commit()

    message_bus.publish(
        user_assigned.UserAssigned(userId=assignment.userId, projectId=assignment.projectId, roles=assignment.roles)
    )
