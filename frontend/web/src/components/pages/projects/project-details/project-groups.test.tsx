/* eslint-disable @typescript-eslint/no-magic-numbers */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { MutableSnapshot, RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProjectGroupAssignment, ProjectGroupRole } from '../../../../services/API/proserve-wb-projects-api';
import { ProjectRoles, selectedProjectState } from '../../../../state';
import { ProjectGroups } from './project-groups';

const group: ProjectGroupAssignment = {
  projectId: 'proj-1',
  groupId: 'engineering/team',
  groupName: 'Engineering',
  roles: [ProjectGroupRole.PlatformUser, ProjectGroupRole.BetaUser],
  version: 1,
  isDeleted: false,
  createDate: '2026-11-01T10:00:00Z',
  lastUpdateDate: '2026-11-01T10:00:00Z',
};

const saveGroup = vi.fn();
const removeGroup = vi.fn();

function renderGroups(roles: ProjectRoles[], groups: ProjectGroupAssignment[] = [group]) {
  const initializeState = ({ set }: MutableSnapshot) => {
    set(selectedProjectState, { projectId: 'proj-1', roles });
  };
  return render(
    <RecoilRoot initializeState={initializeState}>
      <ProjectGroups
        groups={groups}
        groupsLoading={false}
        loadGroups={vi.fn()}
        saveGroup={saveGroup}
        removeGroup={removeGroup}
        groupSaveInProgress={false}
        groupRemoveInProgress={false}
      />
    </RecoilRoot>
  );
}

function button(testId: string) {
  return document.querySelector(`[data-test="${testId}"]`) as HTMLElement | null;
}

function modal(testId: string) {
  return within(button(testId)!);
}

function openRoleOptions() {
  const select = createWrapper(document.body).findMultiselect()!;
  select.openDropdown();
  return select;
}

function optionLabels(select: ReturnType<typeof openRoleOptions>) {
  return select.findDropdown().findOptions().map(o => o.getElement().textContent);
}

function selectFirstRow() {
  fireEvent.click(screen.getAllByRole('radio')[0]);
}

describe('ProjectGroups', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    saveGroup.mockResolvedValue(true);
    removeGroup.mockResolvedValue(true);
  });

  it('lists group id, display name and roles', () => {
    renderGroups([ProjectRoles.ProgramOwner]);

    expect(screen.getByText('engineering/team')).toBeInTheDocument();
    expect(screen.getByText('Engineering')).toBeInTheDocument();
    expect(screen.getByText('Lvl 1 - Platform User')).toBeInTheDocument();
    expect(screen.getByText('Lvl 2 - Beta User')).toBeInTheDocument();
  });

  it('shows add, edit and remove to program owners', () => {
    renderGroups([ProjectRoles.ProgramOwner]);

    expect(button('add-group-button')).toBeInTheDocument();
    expect(button('edit-group-button')).toBeInTheDocument();
    expect(button('remove-group-button')).toBeInTheDocument();
  });

  it('hides add, edit, remove and row selection from users who cannot manage roles', () => {
    renderGroups([ProjectRoles.PlatformUser]);

    expect(screen.getByText('engineering/team')).toBeInTheDocument();
    expect(button('add-group-button')).not.toBeInTheDocument();
    expect(button('edit-group-button')).not.toBeInTheDocument();
    expect(button('remove-group-button')).not.toBeInTheDocument();
    expect(screen.queryAllByRole('radio')).toHaveLength(0);
  });

  it('keeps Add disabled until a group id and a role are given', () => {
    renderGroups([ProjectRoles.Admin], []);

    fireEvent.click(button('add-group-button')!);
    const form = modal('group-form-modal');
    const confirm = form.getByText('Add').closest('button')!;
    expect(confirm).toBeDisabled();

    fireEvent.change(form.getAllByRole('textbox')[0], { target: { value: 'a/b' } });
    expect(confirm).toBeDisabled();
  });

  it('saves a new group with the given id and name', () => {
    renderGroups([ProjectRoles.Admin], []);

    fireEvent.click(button('add-group-button')!);
    const inputs = modal('group-form-modal').getAllByRole('textbox');
    fireEvent.change(inputs[0], { target: { value: 'Engineering/Team' } });
    fireEvent.change(inputs[1], { target: { value: 'Eng' } });
    openRoleOptions().selectOptionByValue('PLATFORM_USER');
    fireEvent.click(modal('group-form-modal').getByText('Add').closest('button')!);

    expect(saveGroup).toHaveBeenCalledWith('Engineering/Team', 'Eng', ['PLATFORM_USER']);
  });

  it('edits the display name and roles, with the group id read-only', () => {
    renderGroups([ProjectRoles.ProgramOwner]);

    selectFirstRow();
    fireEvent.click(button('edit-group-button')!);
    const dialog = modal('group-form-modal');
    const [idInput, nameInput] = dialog.getAllByRole('textbox') as HTMLInputElement[];
    expect(idInput).toHaveValue('engineering/team');
    expect(idInput).toHaveAttribute('readonly');
    fireEvent.change(nameInput, { target: { value: 'Platform engineers' } });
    fireEvent.click(dialog.getByText('Save').closest('button')!);

    expect(saveGroup).toHaveBeenCalledWith(
      'engineering/team', 'Platform engineers', ['PLATFORM_USER', 'BETA_USER']);
  });

  it('removes a group only after confirmation', () => {
    renderGroups([ProjectRoles.ProgramOwner]);

    selectFirstRow();
    fireEvent.click(button('remove-group-button')!);
    expect(removeGroup).not.toHaveBeenCalled();
    const dialog = modal('remove-group-modal');
    expect(dialog.getByText(/engineering\/team/u)).toBeInTheDocument();
    fireEvent.click(dialog.getByText('Remove').closest('button')!);

    expect(removeGroup).toHaveBeenCalledWith('engineering/team');
  });

  it('does not offer Frontend Admin or Program Owner roles to program owners', () => {
    renderGroups([ProjectRoles.ProgramOwner], []);

    fireEvent.click(button('add-group-button')!);

    const labels = optionLabels(openRoleOptions());

    expect(labels.some(l => l?.includes('Lvl 4 - Power User'))).toBe(true);
    expect(labels.some(l => l?.includes('Program Owner'))).toBe(false);
    expect(labels.some(l => l?.includes('Frontend Admin'))).toBe(false);
  });
});
