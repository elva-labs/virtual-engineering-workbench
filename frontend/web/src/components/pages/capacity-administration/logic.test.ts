/* eslint-disable @typescript-eslint/no-magic-numbers, @typescript-eslint/naming-convention, @stylistic/max-len */
import { describe, expect, it } from 'vitest';
import { quotaRows, quotaState } from './logic';
import { capacityHint, CapacityOverview, CapacityQuota } from '../../../services/API/capacity-api';

const quota = (over: Partial<CapacityQuota>): CapacityQuota => ({
  quotaCode: 'L-1216C47A', serviceCode: 'ec2', label: 'Standard vCPU', unit: 'vCPU',
  limit: 60, used: 12, remaining: 48, usedPercent: 20, ...over,
});

describe('capacity logic', () => {
  it('names a zero quota unavailable, a missing one unknown, a full one high', () => {
    expect(quotaState(quota({ limit: 0, usedPercent: 0 }), 80)).toBe('unavailable');
    expect(quotaState(quota({ limit: null, usedPercent: null }), 80)).toBe('unknown');
    expect(quotaState(quota({ usedPercent: 85 }), 80)).toBe('high');
    expect(quotaState(quota({}), 80)).toBe('ok');
  });

  it('lists one row per account and quota, the most used first, with the open request', () => {
    const overview = {
      collectedAt: null, alarmUsedPercent: 80,
      totals: { accounts: 1, programs: 1, workbenchesByState: {}, runningByInstanceType: {}, gpuInstancesRunning: 0, gp3GiB: 0 },
      accounts: [{
        awsAccountId: '533813050837', region: 'eu-north-1', collectedAt: '', gp3GiB: 0, instances: [],
        programs: [{ projectId: 'proj-a', projectName: 'AiPlatform', stages: ['DEV'] }],
        quotas: [quota({}), quota({ quotaCode: 'L-DB2E81BA', limit: 0, usedPercent: 0 }), quota({ quotaCode: 'X', usedPercent: 90 })],
        requests: [{
          awsAccountId: '533813050837', region: 'eu-north-1', serviceCode: 'ec2', quotaCode: 'L-DB2E81BA',
          desiredValue: 16, requestedBy: 'a', requestedAt: 't', status: 'PENDING'
        }],
      }],
    } as CapacityOverview;
    const rows = quotaRows(overview);
    expect(rows.map(r => r.quota.quotaCode)).toEqual(['X', 'L-1216C47A', 'L-DB2E81BA']);
    expect(rows[2].openRequest?.desiredValue).toBe(16);
    expect(rows[0].programs).toBe('AiPlatform');
  });

  it('gives the launch form no hint without data', () => {
    expect(capacityHint(undefined, 'm7i.xlarge').available).toBeNull();
    expect(capacityHint({
      projectId: 'p', accounts: [{
        awsAccountId: 'a', region: 'r', collectedAt: 't', quotas: [],
        available: { 'g6.xlarge': 0 }
      }]
    }, 'g6.xlarge').available).toBe(0);
  });
});
