/* eslint-disable @typescript-eslint/no-magic-numbers */
import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { projectsAPI } from '../../../../services';
import { ProjectGroupAssignment, ProjectGroupRole } from '../../../../services/API/proserve-wb-projects-api';
import { useProjectGroups } from './project-groups.logic';

vi.mock('../../../../services', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../services')>(),
  projectsAPI: {
    getProjectGroups: vi.fn(),
    putProjectGroup: vi.fn(),
    deleteProjectGroup: vi.fn(),
  },
}));

const api = vi.mocked(projectsAPI);

function binding(overrides: Partial<ProjectGroupAssignment> = {}): ProjectGroupAssignment {
  return {
    projectId: 'proj-1',
    groupId: 'engineering/team',
    groupName: 'Engineering',
    roles: [ProjectGroupRole.PlatformUser],
    version: 1,
    isDeleted: false,
    createDate: '2026-11-01T10:00:00Z',
    lastUpdateDate: '2026-11-01T10:00:00Z',
    ...overrides,
  };
}

function httpError(status: number) {
  return { response: new Response('{"message":"nope"}', { status }) };
}

const wrapper = ({ children }: { children: React.ReactNode }) => <RecoilRoot>{children}</RecoilRoot>;

function setup() {
  return renderHook(() => useProjectGroups({ projectId: 'proj-1' }), { wrapper });
}

describe('useProjectGroups', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('loads the bindings and marks the section as available', async () => {
    api.getProjectGroups.mockResolvedValue({
      assignments: [binding(), binding({ groupId: 'old', isDeleted: true })],
    });

    const { result } = setup();

    await waitFor(() => expect(result.current.groupsAvailable).toBe(true));
    expect(api.getProjectGroups).toHaveBeenCalledWith('proj-1');
    expect(result.current.groups.map(g => g.groupId)).toEqual(['engineering/team']);
  });

  it('hides the section when the feature is off (404)', async () => {
    api.getProjectGroups.mockRejectedValue(httpError(404));

    const { result } = setup();

    await waitFor(() => expect(api.getProjectGroups).toHaveBeenCalled());
    await waitFor(() => expect(result.current.groupsLoading).toBe(false));
    expect(result.current.groupsAvailable).toBe(false);
    expect(result.current.groups).toEqual([]);
  });

  it('hides the section without an error when the caller cannot manage roles (403)', async () => {
    api.getProjectGroups.mockRejectedValue(httpError(403));

    const { result } = setup();

    await waitFor(() => expect(result.current.groupsLoading).toBe(false));
    expect(result.current.groupsAvailable).toBe(false);
  });

  it('keeps the section hidden and does not throw on other errors', async () => {
    api.getProjectGroups.mockRejectedValue(httpError(500));

    const { result } = setup();

    await waitFor(() => expect(result.current.groupsLoading).toBe(false));
    expect(result.current.groupsAvailable).toBe(false);
  });

  it('adds a group and reloads', async () => {
    api.getProjectGroups.mockResolvedValue({ assignments: [] });
    api.putProjectGroup.mockResolvedValue(binding());
    const { result } = setup();
    await waitFor(() => expect(result.current.groupsAvailable).toBe(true));
    api.getProjectGroups.mockResolvedValue({ assignments: [binding()] });

    let saved = false;
    await act(async () => {
      saved = await result.current.saveGroup(
        'engineering/team', 'Engineering', [ProjectGroupRole.PlatformUser]);
    });

    expect(saved).toBe(true);
    expect(api.putProjectGroup).toHaveBeenCalledWith('proj-1', {
      groupId: 'engineering/team',
      groupName: 'Engineering',
      roles: ['PLATFORM_USER'],
    });
    expect(result.current.groups).toHaveLength(1);
  });

  it('clears the display name by sending null when it is blank (edit)', async () => {
    api.getProjectGroups.mockResolvedValue({ assignments: [binding()] });
    api.putProjectGroup.mockResolvedValue(binding({ groupName: null }));
    const { result } = setup();
    await waitFor(() => expect(result.current.groupsAvailable).toBe(true));

    await act(async () => {
      await result.current.saveGroup('engineering/team', '  ', [ProjectGroupRole.BetaUser]);
    });

    expect(api.putProjectGroup).toHaveBeenCalledWith('proj-1', {
      groupId: 'engineering/team',
      groupName: null,
      roles: ['BETA_USER'],
    });
  });

  it('reports a failed save and does not reload', async () => {
    api.getProjectGroups.mockResolvedValue({ assignments: [] });
    api.putProjectGroup.mockRejectedValue(httpError(400));
    const { result } = setup();
    await waitFor(() => expect(result.current.groupsAvailable).toBe(true));
    api.getProjectGroups.mockClear();

    let saved = true;
    await act(async () => {
      saved = await result.current.saveGroup('g', '', [ProjectGroupRole.PlatformUser]);
    });

    expect(saved).toBe(false);
    expect(api.getProjectGroups).not.toHaveBeenCalled();
  });

  it('removes a group by its exact, case-sensitive id and reloads', async () => {
    api.getProjectGroups.mockResolvedValue({ assignments: [binding()] });
    api.deleteProjectGroup.mockResolvedValue({});
    const { result } = setup();
    await waitFor(() => expect(result.current.groups).toHaveLength(1));
    api.getProjectGroups.mockResolvedValue({ assignments: [] });

    await act(async () => {
      await result.current.removeGroup('engineering/team');
    });

    expect(api.deleteProjectGroup).toHaveBeenCalledWith('proj-1', { groupId: 'engineering/team' });
    expect(result.current.groups).toEqual([]);
  });
});
