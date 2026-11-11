import {
  Badge,
  Box,
  Button,
  FormField,
  Header,
  Input,
  Multiselect,
  MultiselectProps,
  SpaceBetween,
  Table,
  TableProps,
} from '@cloudscape-design/components';
import { useState } from 'react';
import { useRoleAccessToggle } from '../../../../hooks/role-access-toggle';
import { ProjectGroupAssignment, ProjectGroupRole } from '../../../../services/API/proserve-wb-projects-api';
import { ProjectRoles, RoleBasedFeature } from '../../../../state';
import { RoleAccessToggle } from '../../shared/role-access-toggle';
import { UserPrompt } from '../../shared/user-prompt';
import { i18n } from './project-groups.translations';
import { USER_ROLE_LEVEL_MAP, USER_ROLE_MAP } from './project-users.static';

const MAX_GROUP_ID_LENGTH = 2042;
const EMPTY = 0;

// Frontend Admin and Program Owner are only offered to Frontend Admins, as in the members table.
const RESTRICTED_ROLES = new Set<string>([ProjectRoles.Admin, ProjectRoles.ProgramOwner]);

type ProjectGroupsProps = {
  groups: ProjectGroupAssignment[],
  groupsLoading: boolean,
  loadGroups: () => void,
  saveGroup: (groupId: string, groupName: string, roles: ProjectGroupRole[]) => Promise<boolean>,
  removeGroup: (groupId: string) => Promise<boolean>,
  groupSaveInProgress: boolean,
  groupRemoveInProgress: boolean,
};

export function getGroupRoleName(role: string) {
  const r = role as ProjectRoles;
  if (USER_ROLE_MAP[r]) {
    return `Lvl ${USER_ROLE_LEVEL_MAP[r]} - ${USER_ROLE_MAP[r]}`;
  }
  return role;
}

const COLUMN_DEFINITIONS: TableProps.ColumnDefinition<ProjectGroupAssignment>[] = [
  {
    id: 'groupId',
    header: i18n.tableHeaderGroupId,
    cell: g => g.groupId,
    sortingField: 'groupId',
  },
  {
    id: 'groupName',
    header: i18n.tableHeaderGroupName,
    cell: g => g.groupName ?? '',
    sortingField: 'groupName',
  },
  {
    id: 'roles',
    header: i18n.tableHeaderRoles,
    cell: g => <SpaceBetween size={'xs'} direction='horizontal'>
      {g.roles.map(r => <Badge color="blue" key={r}><b>{getGroupRoleName(r)}</b></Badge>)}
    </SpaceBetween>,
  },
];

function projectGroups({
  groups,
  groupsLoading,
  loadGroups,
  saveGroup,
  removeGroup,
  groupSaveInProgress,
  groupRemoveInProgress,
}: ProjectGroupsProps) {
  const isFeatureAccessible = useRoleAccessToggle();
  const [selectedItems, setSelectedItems] = useState<ProjectGroupAssignment[]>([]);
  const [formMode, setFormMode] = useState<'add' | 'edit' | undefined>(undefined);
  const [formGroupId, setFormGroupId] = useState('');
  const [formGroupName, setFormGroupName] = useState('');
  const [formRoles, setFormRoles] = useState<ReadonlyArray<MultiselectProps.Option>>([]);
  const [removeConfirmVisible, setRemoveConfirmVisible] = useState(false);

  const selected = selectedItems[EMPTY];
  const canManage = isFeatureAccessible(RoleBasedFeature.ReassignRoleOfProgramUser);
  const roleOptions = buildRoleOptions();
  const groupIdLength = formGroupId.trim().length;
  const formGroupIdValid = groupIdLength > EMPTY && groupIdLength <= MAX_GROUP_ID_LENGTH;
  const formRolesValid = formRoles.length > EMPTY;

  return <>
    {renderFormPrompt()}
    {renderRemovePrompt()}
    <Table
      columnDefinitions={COLUMN_DEFINITIONS}
      items={groups}
      loading={groupsLoading || groupRemoveInProgress}
      loadingText={i18n.tableLoading}
      trackBy="groupId"
      selectionType={canManage ? 'single' : undefined}
      selectedItems={selectedItems}
      onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
      empty={<Box textAlign="center" color="inherit">
        <b>{i18n.tableEmptyTitle}</b>
        <Box variant="p" color="inherit">{i18n.tableEmptySubtitle}</Box>
      </Box>}
      header={
        <Header
          counter={`(${groups.length})`}
          description={i18n.tableDescription}
          actions={
            <SpaceBetween size={'xs'} direction={'horizontal'}>
              <Button
                loading={groupsLoading}
                onClick={() => loadGroups()}
                ariaLabel={i18n.buttonRefresh}
                iconName='refresh' />
              <RoleAccessToggle feature={RoleBasedFeature.ReassignRoleOfProgramUser}>
                <Button
                  onClick={openEditForm}
                  disabled={!selected}
                  data-test="edit-group-button">{i18n.buttonEdit}</Button>
                <Button
                  onClick={() => setRemoveConfirmVisible(true)}
                  disabled={!selected}
                  data-test="remove-group-button">{i18n.buttonRemove}</Button>
                <Button
                  onClick={openAddForm}
                  variant='primary'
                  data-test="add-group-button">{i18n.buttonAdd}</Button>
              </RoleAccessToggle>
            </SpaceBetween>
          }
        >
          {i18n.tableHeader}
        </Header>
      }
    />
  </>;

  function buildRoleOptions(): MultiselectProps.Option[] {
    const canManageAdmins = isFeatureAccessible(RoleBasedFeature.ManageRoleFrontendAdmin);
    return Object.values(ProjectRoles)
      .filter(r => canManageAdmins || !RESTRICTED_ROLES.has(r))
      .sort((a, b) => Number(USER_ROLE_LEVEL_MAP[a]) - Number(USER_ROLE_LEVEL_MAP[b]))
      .map(r => ({ value: r, label: getGroupRoleName(r) }));
  }

  function toOption(role: string): MultiselectProps.Option {
    return { value: role, label: getGroupRoleName(role) };
  }

  function openAddForm() {
    setFormGroupId('');
    setFormGroupName('');
    setFormRoles([]);
    setFormMode('add');
  }

  function openEditForm() {
    if (!selected) {
      return;
    }
    setFormGroupId(selected.groupId);
    setFormGroupName(selected.groupName ?? '');
    setFormRoles(selected.roles.map(toOption));
    setFormMode('edit');
  }

  async function handleFormConfirm() {
    const roles = formRoles.map(o => o.value as ProjectGroupRole);
    const saved = await saveGroup(formGroupId.trim(), formGroupName, roles);
    if (saved) {
      setFormMode(undefined);
      setSelectedItems([]);
    }
  }

  async function handleRemoveConfirm() {
    if (!selected) {
      return;
    }
    await removeGroup(selected.groupId);
    setRemoveConfirmVisible(false);
    setSelectedItems([]);
  }

  function renderFormPrompt() {
    const editing = formMode === 'edit';
    const showGroupIdError = !editing && formGroupId !== '' && !formGroupIdValid;
    return <UserPrompt
      onConfirm={handleFormConfirm}
      onCancel={() => setFormMode(undefined)}
      headerText={editing ? i18n.editModalHeader : i18n.addModalHeader}
      content={<SpaceBetween size='l'>
        <FormField
          label={i18n.fieldGroupId}
          description={i18n.fieldGroupIdDescription}
          errorText={showGroupIdError ? i18n.fieldGroupIdError : undefined}
        >
          <Input
            value={formGroupId}
            onChange={({ detail }) => setFormGroupId(detail.value)}
            readOnly={editing}
            data-test="group-id-input"
          />
        </FormField>
        <FormField label={i18n.fieldGroupName} description={i18n.fieldGroupNameDescription}>
          <Input
            value={formGroupName}
            onChange={({ detail }) => setFormGroupName(detail.value)}
            data-test="group-name-input"
          />
        </FormField>
        <FormField label={i18n.fieldRoles} errorText={formRolesValid ? undefined : i18n.fieldRolesError}>
          <Multiselect
            selectedOptions={formRoles}
            onChange={({ detail }) => setFormRoles(detail.selectedOptions)}
            options={roleOptions}
            placeholder={i18n.fieldRolesPlaceholder}
            keepOpen
            data-test="group-roles-select"
          />
        </FormField>
      </SpaceBetween>}
      cancelText={i18n.modalCancel}
      confirmText={editing ? i18n.editModalConfirm : i18n.addModalConfirm}
      confirmButtonLoading={groupSaveInProgress}
      confirmButtonDisabled={!formGroupIdValid || !formRolesValid}
      visible={!!formMode}
      data-test='group-form-modal'
    />;
  }

  function renderRemovePrompt() {
    return <UserPrompt
      onConfirm={handleRemoveConfirm}
      onCancel={() => setRemoveConfirmVisible(false)}
      headerText={i18n.removeModalHeader}
      content={<>
        {i18n.removeModalText1}
        <ul><li><b>{selected?.groupName ? `${selected.groupName} - ` : ''}{selected?.groupId}</b></li></ul>
        {i18n.removeModalText2}
        <p>{i18n.removeModalText3}</p>
      </>}
      cancelText={i18n.modalCancel}
      confirmText={i18n.removeModalConfirm}
      confirmButtonLoading={groupRemoveInProgress}
      visible={removeConfirmVisible}
      data-test='remove-group-modal'
    />;
  }
}

export { projectGroups as ProjectGroups };
