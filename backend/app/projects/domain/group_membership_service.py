from app.projects.domain.commands.users import assign_user_command, reassign_user_command, unassign_user_command
from app.projects.domain.value_objects import project_id_value_object, user_id_value_object, user_role_value_object
from app.shared.identity import oidc_groups


class GroupMembershipService:
    def __init__(self, query_service, command_bus):
        self._query = query_service
        self._commands = command_bus

    def sign_in(self, user_id: str, profile: dict):
        if not oidc_groups.provider_name():
            return
        user_id = user_id.upper()
        group_ids = oidc_groups.trusted_group_ids(profile)
        bindings = self._query.get_group_assignments(group_ids)
        project_ids = {binding.projectId for binding in bindings}
        project_ids.update(
            assignment.projectId
            for assignment in self._query.list_user_assignments(user_id)
            if assignment.source == "GROUP"
        )
        for project_id in sorted(project_ids):
            self._reconcile(user_id, project_id, group_ids, bindings, create=True)

    def binding_changed(self, changed):
        if not oidc_groups.provider_name():
            return
        # Use base-table reads for bindings: the inverse index may still contain
        # the previous version immediately after a binding write.
        for assignment in self._query.list_users_by_project(changed.projectId):
            if assignment.source != "GROUP" or changed.groupId not in assignment.trustedGroupIds:
                continue
            bindings = self._query.get_group_assignments(assignment.trustedGroupIds)
            bindings = [b for b in bindings if (b.projectId, b.groupId) != (changed.projectId, changed.groupId)]
            if not changed.isDeleted:
                bindings.append(changed)
            self._reconcile(assignment.userId, changed.projectId, assignment.trustedGroupIds, bindings, create=False)

    def _reconcile(self, user_id, project_id, group_ids, bindings, create):
        current = self._query.get_user_assignment(project_id, user_id)
        if current is not None and current.source != "GROUP":
            return
        grants = [b for b in bindings if b.projectId == project_id and b.groupId in group_ids and b.roles]
        roles = sorted({role for b in grants for role in b.roles})
        # Retain the complete trusted group list. A later binding change may
        # grant roles through a group that had no binding at the last sign-in.
        stored_groups = sorted(set(group_ids))
        granting_groups = sorted({b.groupId for b in grants})
        project = project_id_value_object.from_str(project_id)
        user = user_id_value_object.from_str(user_id)
        if not roles:
            if current is not None:
                self._commands.handle(
                    unassign_user_command.UnAssignUserCommand(project_id=project, user_ids=[user], group_only=True)
                )
        elif current is None:
            if create:
                self._commands.handle(
                    assign_user_command.AssignUserCommand(
                        project_id=project,
                        user_id=user,
                        roles=[user_role_value_object.from_str(role) for role in roles],
                        group_ids=granting_groups,
                        trusted_group_ids=stored_groups,
                    )
                )
        elif (
            set(current.roles) != set(roles)
            or current.groupIds != granting_groups
            or current.trustedGroupIds != stored_groups
        ):
            self._commands.handle(
                reassign_user_command.ReAssignUserCommand(
                    project_id=project,
                    user_ids=[user],
                    initiating_user_id=user_id_value_object.from_str(
                        "membership", user_id_value_object.UserIdType.Service
                    ),
                    roles=[user_role_value_object.from_str(role) for role in roles],
                    group_ids=granting_groups,
                    trusted_group_ids=stored_groups,
                )
            )
