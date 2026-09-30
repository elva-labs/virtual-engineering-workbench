import { describe, expect, it } from 'vitest';
import {
  GetProjectsResponseFromJSON,
  type GetProjectsResponse
} from '../services/API/proserve-wb-projects-api';
import { projectRoles } from './project-access';

describe('project selection roles', () => {
  it('selects a group-only project using effective access', () => {
    const response = GetProjectsResponseFromJSON({
      projects: [{ projectId: 'project-1' }],
      assignments: [],
      effectiveAccess: [{ projectId: 'project-1', roles: ['platform_user'] }]
    });
    expect(projectRoles(response, 'project-1')).toEqual(['PLATFORM_USER']);
  });

  it('uses the effective union for mixed grants', () => {
    const response = GetProjectsResponseFromJSON({
      projects: [{ projectId: 'project-1' }],
      assignments: [{ projectId: 'project-1', roles: ['PLATFORM_USER'] }],
      effectiveAccess: [{ projectId: 'project-1', roles: ['PLATFORM_USER', 'ADMIN'] }]
    });
    expect(projectRoles(response, 'project-1')).toEqual(['PLATFORM_USER', 'ADMIN']);
  });

  it('does not fall back to direct grants when effective access is empty', () => {
    const response: GetProjectsResponse = {
      projects: [],
      assignments: [{ projectId: 'project-1', roles: ['ADMIN'] }],
      effectiveAccess: []
    };
    expect(projectRoles(response, 'project-1')).toEqual([]);
  });

  it('uses direct assignments for a legacy response', () => {
    const response: GetProjectsResponse = {
      projects: [],
      assignments: [{ projectId: 'project-1', roles: ['admin'] }]
    };
    expect(projectRoles(response, 'project-1')).toEqual(['ADMIN']);
  });
});
