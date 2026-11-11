import { useEffect, useState } from 'react';
import { projectsAPI } from '../../../../services';
import { ProjectGroupAssignment, ProjectGroupRole } from '../../../../services/API/proserve-wb-projects-api';
import { extractErrorResponseMessage } from '../../../../utils/api-helpers';
import { useNotifications } from '../../../layout';
import { i18n } from './project-groups.translations';

const HTTP_FORBIDDEN = 403;
const HTTP_NOT_FOUND = 404;

type ProjectGroupsProps = {
  projectId: string,
};

// The routes answer 404 when group grants are switched off and 403 when the caller may not manage roles.
// In both cases there is nothing to show.
function isSectionUnavailable(e: unknown) {
  const status = (e as { response?: { status?: number } })?.response?.status;
  return status === HTTP_NOT_FOUND || status === HTTP_FORBIDDEN;
}

const useProjectGroups = ({ projectId }: ProjectGroupsProps) => {
  const [groups, setGroups] = useState<ProjectGroupAssignment[]>([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [groupsAvailable, setGroupsAvailable] = useState(false);
  const [groupSaveInProgress, setGroupSaveInProgress] = useState(false);
  const [groupRemoveInProgress, setGroupRemoveInProgress] = useState(false);

  const { showErrorNotification, showSuccessNotification } = useNotifications();

  useEffect(() => {
    loadGroups();
  }, []);

  return {
    groups,
    groupsLoading,
    groupsAvailable,
    loadGroups,
    saveGroup,
    removeGroup,
    groupSaveInProgress,
    groupRemoveInProgress,
  };

  function loadGroups() {
    setGroupsLoading(true);

    return projectsAPI.
      getProjectGroups(projectId).
      then(response => {
        setGroups((response.assignments || []).filter(g => !g.isDeleted));
        setGroupsAvailable(true);
      }).catch(async e => {
        if (isSectionUnavailable(e)) {
          setGroupsAvailable(false);
          return;
        }
        showErrorNotification({
          header: i18n.fetchErrorHeader,
          content: await extractErrorResponseMessage(e)
        });
      }).finally(() => {
        setGroupsLoading(false);
      });
  }

  function saveGroup(groupId: string, groupName: string, roles: ProjectGroupRole[]) {
    setGroupSaveInProgress(true);

    return projectsAPI.
      putProjectGroup(projectId, { groupId, groupName: groupName.trim() || null, roles }).
      then(() => {
        showSuccessNotification({
          header: i18n.saveSuccess,
          content: i18n.saveSuccessContent,
        });
        return loadGroups().then(() => true);
      }).catch(async e => {
        showErrorNotification({
          header: i18n.saveError,
          content: await extractErrorResponseMessage(e)
        });
        return false;
      }).finally(() => {
        setGroupSaveInProgress(false);
      });
  }

  function removeGroup(groupId: string) {
    setGroupRemoveInProgress(true);

    return projectsAPI.
      deleteProjectGroup(projectId, { groupId }).
      then(() => {
        showSuccessNotification({
          header: i18n.removeSuccess,
          content: i18n.removeSuccessContent,
        });
        return loadGroups().then(() => true);
      }).catch(async e => {
        showErrorNotification({
          header: i18n.removeError,
          content: await extractErrorResponseMessage(e)
        });
        return false;
      }).finally(() => {
        setGroupRemoveInProgress(false);
      });
  }
};

export { useProjectGroups, isSectionUnavailable };
