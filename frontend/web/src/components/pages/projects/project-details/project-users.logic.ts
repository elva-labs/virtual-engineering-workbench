import { useEffect, useState } from 'react';
import { projectsAPI } from '../../../../services';
import type { ProjectGroupAssignment } from '../../../../services/API/projects-api';
import {
  GetProjectAssignmentsResponseItem,
  ProjectGroupMember,
} from '../../../../services/API/proserve-wb-projects-api';
import { extractErrorResponseMessage } from '../../../../utils/api-helpers';
import { useNotifications } from '../../../layout';

const i18n = {
  userFetchErrorHeader: 'Unable to fetch project users.',
  groupFetchErrorHeader: 'Unable to fetch project groups.',
  userUnassignSuccess: 'The user has been successfully offboarded from the program',
  userUnassignError: 'Unable to unassign users',
  // eslint-disable-next-line @stylistic/max-len
  userUnassignSuccessContent: 'The user will then be informed by email about their offboarding from the program. Please refer to the table below to verify the status.'
};

type ProjectUsersProps = {
  projectId: string,
};

/** A member row: a direct assignment, a member through a group binding, or both. */
export type MemberRow = GetProjectAssignmentsResponseItem & { direct?: boolean, viaGroupIds?: string[] };

/** Direct assignments plus members known through a bound group since their first sign-in. */
export function withGroupMembers(
  assignments: GetProjectAssignmentsResponseItem[], groupMembers: ProjectGroupMember[]
): MemberRow[] {
  const rows = new Map<string, MemberRow>(assignments.map(a => [a.userId ?? '', { ...a, direct: true }]));
  for (const member of groupMembers) {
    const existing = rows.get(member.userId);
    if (existing) {
      existing.viaGroupIds = member.groupIds;
    } else {
      rows.set(member.userId, {
        userId: member.userId,
        userEmail: member.userEmail ?? undefined,
        roles: member.roles,
        direct: false,
        viaGroupIds: member.groupIds,
      });
    }
  }
  return [...rows.values()];
}

/** Only through a group: roles and removal are managed in Entra and Terraform, not here. */
export function isGroupOnly(row: MemberRow): boolean {
  return row.direct === false;
}

const useProjectUsers = ({ projectId }: ProjectUsersProps) => {
  const [projectUsers, setProjectUsers] = useState<MemberRow[]>([]);
  const [projectGroups, setProjectGroups] = useState<ProjectGroupAssignment[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [userUnassignInProgress, setUserUnassignInProgress] = useState(false);

  const { showErrorNotification, showSuccessNotification } = useNotifications();

  useEffect(() => {
    loadProjectUsers();
    loadProjectGroups();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  return {
    projectUsers,
    projectGroups,
    usersLoading: usersLoading,
    loadProjectUsers,
    loadProjectGroups,
    unassignUsers,
    userUnassignInProgress,
  };

  function loadProjectUsers() {
    setUsersLoading(true);

    projectsAPI.
      getProjectUsers(projectId).
      then(response => {
        setProjectUsers(withGroupMembers(response.assignments || [], response.groupMembers || []));
      }).catch(async e => {
        showErrorNotification({
          header: i18n.userFetchErrorHeader,
          content: await extractErrorResponseMessage(e)
        });
      }).finally(() => {
        setUsersLoading(false);
      });
  }

  function loadProjectGroups() {
    setProjectGroups([]);
    projectsAPI.getProjectGroups(projectId).then(response => {
      setProjectGroups(response.assignments ?? []);
    }).catch(async e => {
      showErrorNotification({
        header: i18n.groupFetchErrorHeader,
        content: await extractErrorResponseMessage(e)
      });
    });
  }

  function unassignUsers(userIds: string[]) {

    setUserUnassignInProgress(true);

    projectsAPI.removeProjectUsers(projectId, { userIds: userIds })
      .then(() => {
        showSuccessNotification({
          header: i18n.userUnassignSuccess,
          content: i18n.userUnassignSuccessContent,
        });
      })
      .then(() => {
        loadProjectUsers();
      })
      .catch(async (e) => {
        showErrorNotification({
          header: i18n.userUnassignError,
          content: await extractErrorResponseMessage(e)
        });
      })
      .finally(() => {
        setUserUnassignInProgress(false);
      });
  }
};

export { useProjectUsers };
