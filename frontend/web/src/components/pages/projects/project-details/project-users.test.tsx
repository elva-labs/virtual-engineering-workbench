import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { describe, expect, it, vi } from 'vitest';
import { ProjectRoles, selectedProjectState } from '../../../../state';
import { GetProjectAssignmentsResponseItemFromJSON } from '../../../../services/API/proserve-wb-projects-api';
import { ProjectUsers } from './project-users';

vi.mock('../../../layout/navigation/navigation-paths.logic', () => ({
  useNavigationPaths: () => ({ navigateTo: vi.fn() }),
}));

vi.mock('../project-user-reassignment/project-user-reassignment.logic', () => ({
  useProjectUserReAssignment: () => ({
    userReAssignmentInProgress: false,
    reAssignUsers: vi.fn(),
    memberRoleLevel: [],
    setMemberRoleLevel: vi.fn(),
  }),
}));

function renderMembers(source?: string) {
  return render(<RecoilRoot initializeState={({ set }) => {
    set(selectedProjectState, { projectId: 'project', roles: [ProjectRoles.Admin] });
  }}>
    <ProjectUsers
      projectUsers={[GetProjectAssignmentsResponseItemFromJSON({
        userId: 'member', userEmail: 'member@example.com', roles: ['PLATFORM_USER'],
        source, groupIds: ['engineering/team'],
      })]}
      usersLoading={false}
      loadProjectUsers={vi.fn()}
      unassignUsers={vi.fn()}
      userUnassignInProgress={false}
    />
  </RecoilRoot>);
}

describe('group-sourced project members', () => {
  it('keeps the source and group ids in the hand-written client and shows the group marker', () => {
    renderMembers('GROUP');
    expect(screen.getByText('via group engineering/team')).toBeInTheDocument();
  });

  it('does not mark legacy direct members', () => {
    renderMembers();
    expect(screen.queryByText('via group engineering/team')).not.toBeInTheDocument();
  });

  it('warns that removal cleans up resources but does not block group access', () => {
    renderMembers('GROUP');
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes.pop()!);
    fireEvent.click(screen.getByText('Offboard member(s)'));
    expect(screen.getByText(/Membership returns at the next sign-in/u)).toBeVisible();
    expect(screen.getByText(/Remove the group binding to stop that access/u)).toBeVisible();
  });
});
