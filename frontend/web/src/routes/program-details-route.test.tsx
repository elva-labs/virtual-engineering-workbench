import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Navigation } from '../components/layout/navigation/navigation';
import { NavigationPreset } from '../components/layout/navigation/navigation-preset.logic';
import { loggedInUserState } from '../components/session-management/logged-user';
import { projectWorkbenchLifecycleAPI } from '../services/API/project-workbench-lifecycle-api';
import {
  ProjectRoles, projectsState, selectedProjectState, SelectedProject,
} from '../state';
import { projectsLoadedState } from '../state/projects-loaded-state';
import { RouterAuth } from './router-auth';

// Keep the real route, navigation, role gates, selected-project hook and shutdown form.
// Replace the app chrome and profile feature fetch so this exercises routing without unrelated services.
vi.mock('../components/layout/workbench-app-layout/workbench-app-layout', () => {
  const WorkbenchAppLayout = ({ customHeader, content }: { customHeader?: ReactNode, content?: ReactNode }) =>
    <main>{customHeader}{content}</main>;
  return { WorkbenchAppLayout };
});
vi.mock('../components/feature-toggles/feature-toggle.hook', () => ({
  useFeatureToggles: () => ({ isFeatureEnabled: () => false, featuresInitialised: true }),
}));

const PLATFORM_IDLE_MINUTES = 60;
const user = {
  userId: 'user-1', userName: 'user', firstName: 'Test', lastName: 'User', email: 'test@example.com',
};
const load = vi.spyOn(projectWorkbenchLifecycleAPI, 'get');

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('ExpandedNavGroup', JSON.stringify({ Administration: true }));
  NavigationPreset.instance = undefined;
  load.mockReset();
});

function renderPortal(roles: ProjectRoles[], path = '/help', selected = true, canEdit = false) {
  const project: SelectedProject = selected ? {
    projectId: 'project-1', projectName: 'Example', projectDescription: 'Selected program',
    isActive: true, roles,
  } : {};
  load.mockResolvedValue({
    projectId: 'project-1', settings: null, canEdit,
    platformDefaults: {
      idleStopMinutes: PLATFORM_IDLE_MINUTES, nightlyStop: false, nightlyStopTime: '21:00',
      timezone: 'UTC', weekendStop: true,
    },
    deployment: { idleStopEnabled: true, nightlyStopScheduleEnabled: true },
  });
  return render(<RecoilRoot initializeState={({ set }) => {
    set(loggedInUserState, { user });
    set(projectsLoadedState, true);
    set(selectedProjectState, project);
    set(projectsState, selected ? [{
      id: 'project-1', name: 'Example', description: 'Selected program', isActive: true, roles,
    }] : []);
  }}>
    <MemoryRouter initialEntries={[path]}>
      <Navigation user={user} />
      <RouterAuth />
    </MemoryRouter>
  </RecoilRoot>);
}

describe('Program details portal route', () => {
  it('opens the selected program shutdown form from Administration → Program details', async() => {
    renderPortal([ProjectRoles.ProgramOwner], '/help', true, true);
    const link = screen.getByRole('link', { name: 'Program details' });
    expect(link).toHaveAttribute('href', '/administration/program');
    expect(screen.queryByText('Effective project defaults')).toBeNull();
    fireEvent.click(link);
    expect(await screen.findByRole('heading', { name: 'Example program' })).toBeTruthy();
    expect(await screen.findByText('Effective project defaults')).toBeTruthy();
    await waitFor(() => expect(load).toHaveBeenCalledWith('project-1'));
    expect(screen.getByRole('tab', { name: 'Shutdown policy' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: 'Save policy' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Update' })).toBeNull();
    expect(screen.queryByRole('tab', { name: 'Accounts' })).toBeNull();
    expect(link).toHaveAttribute('aria-current', 'page');
  });

  it.each([
    ProjectRoles.PlatformUser, ProjectRoles.PowerUser, ProjectRoles.ProductContributor, ProjectRoles.BetaUser,
  ])('supports direct route access and read-only policy visibility for %s', async(role) => {
    const { container } = renderPortal([role], '/administration/program');
    expect(await screen.findByText('60 minutes (platform default)')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Program details' })).toBeTruthy();
    expect(screen.getByText('Only administrators and owners of this program can edit its shutdown policy.'))
      .toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save policy' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Update' })).toBeNull();
    expect([...container.querySelectorAll('[data-test^="project-shutdown-"] input')]
      .every(input => (input as HTMLInputElement).disabled)).toBe(true);
  });

  it('retains admin-only account and program update gates', async() => {
    renderPortal([ProjectRoles.Admin], '/administration/program', true, true);
    await screen.findByText('Effective project defaults');
    expect(screen.getByRole('button', { name: 'Update' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Accounts' })).toBeTruthy();
  });

  it('offers program selection without fetching a policy when no program is selected', async() => {
    renderPortal([], '/administration/program', false);
    expect(screen.getByText('Choose a program to view its details and shutdown policy.')).toBeTruthy();
    expect(screen.queryByText('undefined program')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save policy' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Update' })).toBeNull();
    expect(load).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Choose program' }));
    expect(await screen.findByRole('heading', { name: 'Programs' })).toBeTruthy();
  });
});
