import { SelectProps } from '@cloudscape-design/components';
import {
  CapacityOverview, CapacityQuota, ProjectCapacity, QuotaIncreaseRequest,
} from '../../../services/API/capacity-api';
import { i18n } from './translations';

export const DEFAULT_ALARM_PERCENT = 80;
const GIB_PER_TIB = 1024;
const TIB_DECIMALS = 10;
const NO_PERCENT = -1;
const NOTHING = 0;
// A first GPU request: four g6.xlarge (4 vCPU each); otherwise double the current quota.
const MIN_VCPU_REQUEST = 16;
const MIN_TIB_REQUEST = 1;
const DOUBLE = 2;

export interface QuotaRow {
  awsAccountId: string,
  region: string,
  programs: string,
  quota: CapacityQuota,
  openRequest?: QuotaIncreaseRequest,
}

const OPEN = new Set(['PENDING', 'CASE_OPENED']);

/** One row per account and quota, the most used first; the open request of each, if any. */
export function quotaRows(overview: CapacityOverview | undefined): QuotaRow[] {
  const rows: QuotaRow[] = [];
  overview?.accounts.forEach(account => {
    const programs = account.programs.map(p => p.projectName || p.projectId).join(', ');
    account.quotas.forEach(quota => rows.push({
      awsAccountId: account.awsAccountId,
      region: account.region,
      programs,
      quota,
      openRequest: account.requests.find(r => r.quotaCode === quota.quotaCode && OPEN.has(r.status)),
    }));
  });
  const percent = (row: QuotaRow) => row.quota.usedPercent ?? NO_PERCENT;
  return rows.sort((a, b) => percent(b) - percent(a));
}

export type QuotaState = 'unknown' | 'unavailable' | 'high' | 'ok';

/** unknown: not readable; unavailable: a zero quota (GPU by default); high: at or above the alarm. */
export function quotaState(quota: CapacityQuota, alarmPercent: number): QuotaState {
  if (quota.limit === null || quota.limit === undefined) {
    return 'unknown';
  }
  if (quota.limit <= NOTHING) {
    return 'unavailable';
  }
  return (quota.usedPercent ?? NOTHING) >= alarmPercent ? 'high' : 'ok';
}

export function suggestedTotal(quota: CapacityQuota): number {
  const minimum = quota.unit === 'vCPU' ? MIN_VCPU_REQUEST : MIN_TIB_REQUEST;
  return Math.max((quota.limit ?? NOTHING) * DOUBLE, minimum);
}

export function tib(gib: number): number {
  return Math.round(gib / GIB_PER_TIB * TIB_DECIMALS) / TIB_DECIMALS;
}

export function programOptions(overview: CapacityOverview | undefined): SelectProps.Option[] {
  const seen = new Map<string, string>();
  overview?.accounts.forEach(a => a.programs.forEach(
    p => seen.set(p.projectId, p.projectName || p.projectId)
  ));
  return Array.from(seen.entries()).map(([value, label]) => ({ value, label }));
}

const UNKNOWN = '-';

export function overviewTiles(overview: CapacityOverview | undefined): { label: string, value: string }[] {
  const totals = overview?.totals;
  if (!totals) {
    return [i18n.programs, i18n.accounts, i18n.running, i18n.stopped, i18n.gpuRunning, i18n.gp3]
      .map(label => ({ label, value: UNKNOWN }));
  }
  const byState = totals.workbenchesByState || {};
  return [
    { label: i18n.programs, value: String(totals.programs) },
    { label: i18n.accounts, value: String(totals.accounts) },
    { label: i18n.running, value: String(byState.running || NOTHING) },
    { label: i18n.stopped, value: String(byState.stopped || NOTHING) },
    { label: i18n.gpuRunning, value: String(totals.gpuInstancesRunning) },
    { label: i18n.gp3, value: `${tib(totals.gp3GiB)} TiB` },
  ];
}

/** The program view's rows: the quotas of the program's own account(s). */
export function projectQuotaRows(capacity: ProjectCapacity | undefined, programName?: string): QuotaRow[] {
  const rows: QuotaRow[] = [];
  capacity?.accounts.forEach(account => account.quotas.forEach(quota => rows.push({
    awsAccountId: account.awsAccountId,
    region: account.region,
    programs: programName || capacity.projectId,
    quota,
  })));
  return rows;
}

// Common workbench sizes: 4, 8 and 16 vCPUs, for standard and GPU workbenches alike.
const SMALL_VCPUS = 4;
const MEDIUM_VCPUS = 8;
const LARGE_VCPUS = 16;
const SIZE_VCPUS = { small: SMALL_VCPUS, medium: MEDIUM_VCPUS, large: LARGE_VCPUS };

export interface FitsRow { family: string, small: number | null, medium: number | null, large: number | null }

function fits(remaining: number | null, vcpus: number): number | null {
  return remaining === null ? null : Math.max(Math.floor(remaining / vcpus), NOTHING);
}

/** How many more workbenches of each size fit each vCPU quota of the program's account. */
export function fitsPerSize(capacity: ProjectCapacity | undefined): FitsRow[] {
  return (capacity?.accounts ?? []).flatMap(account => account.quotas
    .filter(q => q.unit === 'vCPU')
    .map(q => ({
      family: q.label,
      small: fits(q.remaining, SIZE_VCPUS.small),
      medium: fits(q.remaining, SIZE_VCPUS.medium),
      large: fits(q.remaining, SIZE_VCPUS.large),
    })));
}
