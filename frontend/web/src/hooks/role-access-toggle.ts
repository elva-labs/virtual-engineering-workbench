import { RoleBasedFeature, roleAccessToggleState, selectedProjectState, SelectedProject } from '../state';
import { useRecoilState, useRecoilValue } from 'recoil';

export const WORKBENCH_ONLY_EXPERIENCE = 'workbench-only';

// What a workbench-only program doesn't offer its members (everyone but an ADMIN): product
// management, virtual targets, stages and the program's administration pages. The hub enforces the same.
export const WORKBENCH_ONLY_HIDDEN_FEATURES: RoleBasedFeature[] = [
  RoleBasedFeature.ManageProducts,
  RoleBasedFeature.ManageProdProducts,
  RoleBasedFeature.ArchiveProducts,
  RoleBasedFeature.ListAllPipelines,
  RoleBasedFeature.Pipelines,
  RoleBasedFeature.ManageMandatoryComponents,
  RoleBasedFeature.ProductPackagingForceReleaseComponent,
  RoleBasedFeature.ProductPackagingForceReleaseRecipe,
  RoleBasedFeature.ListMyVirtualTargets,
  RoleBasedFeature.RemoveVirtualTarget,
  RoleBasedFeature.ProvisionVirtualTarget,
  RoleBasedFeature.ChooseStageInProductSelection,
  RoleBasedFeature.ListAllProducts,
  RoleBasedFeature.ListProdAndQaProducts,
  RoleBasedFeature.ProvisionExperimentalWorkbench,
  // Members stays: program admins manage who is in the program.
  RoleBasedFeature.ManageTechnologies,
  RoleBasedFeature.ProvisionedProductsAdministration,
];

const ADMIN_ROLE = 'ADMIN';

export function isWorkbenchOnly(project: SelectedProject): boolean {
  return project.experience === WORKBENCH_ONLY_EXPERIENCE && !(project.roles ?? []).includes(ADMIN_ROLE);
}

export function useWorkbenchOnly(): boolean {
  return isWorkbenchOnly(useRecoilValue(selectedProjectState));
}

export function useRoleAccessToggle(): (projectId: RoleBasedFeature) => boolean {
  const accessibleFeatures = useRecoilValue(roleAccessToggleState);

  const selectedProject = useRecoilState(selectedProjectState);
  const assignedRoles = selectedProject[0].roles;
  const workbenchOnly = isWorkbenchOnly(selectedProject[0]);

  function isFeatureAccessible(feature: RoleBasedFeature): boolean {
    if (workbenchOnly && WORKBENCH_ONLY_HIDDEN_FEATURES.includes(feature)) {
      return false;
    }
    let doRoleArraysOverlap = false;
    accessibleFeatures.forEach((feat) => {
      if (feat.feature === feature) {
        const accessRolesForFeature = feat.rolesWithAccess;
        if (assignedRoles) {
          doRoleArraysOverlap = accessRolesForFeature.some(role => assignedRoles.includes(role));
        }
      }
    });

    return doRoleArraysOverlap;
  }
  return isFeatureAccessible;
}
