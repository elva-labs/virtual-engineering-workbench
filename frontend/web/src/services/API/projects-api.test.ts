import { describe, expect, it } from 'vitest';
import { fetchAllProjectPages } from './projects-api';

describe('project list pagination', () => {
  it('keeps group-only access from later pages and deduplicates overlap', async() => {
    const nextOffset = 1;
    const pages = [
      {
        projects: [{ projectId: 'project-1' }],
        assignments: [{ projectId: 'project-1', roles: ['ADMIN'] }],
        effectiveAccess: [{ projectId: 'project-1', roles: ['ADMIN'] }],
        nextToken: { offset: nextOffset }
      },
      {
        projects: [{ projectId: 'project-1' }, { projectId: 'project-2' }],
        assignments: [],
        effectiveAccess: [{ projectId: 'project-2', roles: ['PLATFORM_USER'] }],
        nextToken: undefined
      }
    ];
    const result = await fetchAllProjectPages(() => Promise.resolve(pages.shift()!));
    expect(result.projects.map(project => project.projectId)).toEqual(['project-1', 'project-2']);
    expect(result.effectiveAccess).toEqual([
      { projectId: 'project-1', roles: ['ADMIN'] },
      { projectId: 'project-2', roles: ['PLATFORM_USER'] }
    ]);
    expect(result.assignments).toEqual([{ projectId: 'project-1', roles: ['ADMIN'] }]);
  });

  it('preserves absent effective access for legacy direct fallback', async() => {
    const result = await fetchAllProjectPages(() => Promise.resolve({
      projects: [], assignments: [{ projectId: 'project-1', roles: ['ADMIN'] }]
    }));
    expect(result.effectiveAccess).toBeUndefined();
  });
});
