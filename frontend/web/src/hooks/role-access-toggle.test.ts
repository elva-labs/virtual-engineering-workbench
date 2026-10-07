import { describe, expect, it } from 'vitest';
import { ProjectFromJSON } from '../services/API/proserve-wb-projects-api';
import { RoleBasedFeature } from '../state';
import { isWorkbenchOnly, WORKBENCH_ONLY_HIDDEN_FEATURES } from './role-access-toggle';

// A workbench-only program offers its members (everyone but an ADMIN) their workbenches only.
describe('workbench-only programs', () => {
  it('applies to program owners and users', () => {
    const owner = { experience: 'workbench-only', roles: ['PROGRAM_OWNER', 'PLATFORM_USER'] };
    expect(isWorkbenchOnly(owner)).toBe(true);
    expect(isWorkbenchOnly({ experience: 'workbench-only', roles: ['PLATFORM_USER'] })).toBe(true);
  });

  it('keeps the full portal for admins and for full programs', () => {
    expect(isWorkbenchOnly({ experience: 'workbench-only', roles: ['ADMIN'] })).toBe(false);
    expect(isWorkbenchOnly({ experience: 'full', roles: ['PLATFORM_USER'] })).toBe(false);
    expect(isWorkbenchOnly({ roles: ['PLATFORM_USER'] })).toBe(false);
  });

  it('hides product management, virtual targets and stages', () => {
    expect(WORKBENCH_ONLY_HIDDEN_FEATURES).toEqual(expect.arrayContaining([
      RoleBasedFeature.ManageProducts,
      RoleBasedFeature.ProvisionVirtualTarget,
      RoleBasedFeature.ListMyVirtualTargets,
      RoleBasedFeature.ChooseStageInProductSelection,
    ]));
    expect(WORKBENCH_ONLY_HIDDEN_FEATURES).not.toContain(RoleBasedFeature.ProvisionWorkbench);
    expect(WORKBENCH_ONLY_HIDDEN_FEATURES).not.toContain(RoleBasedFeature.ListMyWorkbenches);
  });

  it('reads the experience from the projects API', () => {
    const project = ProjectFromJSON({ projectId: 'proj-1', experience: 'workbench-only' });
    expect(project.experience).toBe('workbench-only');
  });
});
