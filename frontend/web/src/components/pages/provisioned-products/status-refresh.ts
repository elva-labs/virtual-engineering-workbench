// VEW pushes no status updates to the portal, so a workbench that is starting, stopping or being
// provisioned would keep its old status until the page is reloaded. While any shown workbench is
// in such a state, poll the API; once all of them have settled, stop polling.

// Transitional statuses of the provisioning API (backend ProductStatus).
export const TRANSITIONAL_STATUSES: ReadonlySet<string> = new Set([
  'STARTING',
  'PROVISIONING',
  'STOPPING',
  'SHUTTING_DOWN',
  'DEPROVISIONING',
  'UPDATING',
  'CONFIGURATION_IN_PROGRESS',
]);

export const STATUS_REFRESH_INTERVAL_MS = 10_000;
// SWR does not poll when refreshInterval is 0.
export const NO_STATUS_REFRESH = 0;

export function isTransitional(status?: string): boolean {
  return status !== undefined && TRANSITIONAL_STATUSES.has(status.toUpperCase());
}

// For SWR's refreshInterval, which accepts a function of the latest data.
export function statusRefreshInterval(
  statuses: readonly (string | undefined)[]
): number {
  return statuses.some(isTransitional) ? STATUS_REFRESH_INTERVAL_MS : NO_STATUS_REFRESH;
}
