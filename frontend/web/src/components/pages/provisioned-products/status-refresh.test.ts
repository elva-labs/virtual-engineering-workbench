import { describe, expect, it } from 'vitest';
import {
  isTransitional,
  statusRefreshInterval,
  NO_STATUS_REFRESH,
  STATUS_REFRESH_INTERVAL_MS,
} from './status-refresh.ts';

describe('status refresh', () => {
  it.each([
    'STARTING',
    'PROVISIONING',
    'STOPPING',
    'SHUTTING_DOWN',
    'DEPROVISIONING',
    'UPDATING',
    'CONFIGURATION_IN_PROGRESS',
  ])('treats %s as transitional', (status) => {
    expect(isTransitional(status)).toBe(true);
  });

  it.each([
    'RUNNING',
    'STOPPED',
    'TERMINATED',
    'PROVISIONING_ERROR',
    'CONFIGURATION_FAILED',
    undefined,
  ])('treats %s as settled', (status) => {
    expect(isTransitional(status)).toBe(false);
  });

  it('polls while any workbench is transitional', () => {
    expect(statusRefreshInterval(['RUNNING', 'STOPPING'])).toBe(
      STATUS_REFRESH_INTERVAL_MS
    );
  });

  it('stops polling once every workbench has settled', () => {
    expect(statusRefreshInterval(['RUNNING', 'STOPPED'])).toBe(NO_STATUS_REFRESH);
    expect(statusRefreshInterval([])).toBe(NO_STATUS_REFRESH);
  });
});
