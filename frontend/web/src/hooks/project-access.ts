import type { GetProjectsResponse } from '../services/API/proserve-wb-projects-api';

export function projectRoles(data: GetProjectsResponse, projectId: string): string[] {
  const grants = data.effectiveAccess === undefined ? data.assignments : data.effectiveAccess;
  return (grants?.find(grant => grant.projectId === projectId)?.roles ?? [])
    .map(role => role.toUpperCase());
}
