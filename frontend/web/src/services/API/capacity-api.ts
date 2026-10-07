// Spoke capacity: the spokes' quotas, what uses them, and quota increase requests.
// Plain fetch on the provisioning API (the routes return JSON documents, no generated client).
import { Amplify } from 'aws-amplify';
import { getAccessToken } from '..';

const PROVISIONING_API_NAME = 'ProvisioningAPI';

export interface CapacityQuota {
  quotaCode: string,
  serviceCode: string,
  label: string,
  unit: string,
  limit: number | null,
  used: number,
  remaining: number | null,
  usedPercent: number | null,
}

export interface CapacityInstance {
  instanceType: string,
  state: string,
  count: number,
  vcpus: number,
  workbenches: number,
}

export interface QuotaIncreaseRequest {
  awsAccountId: string,
  region: string,
  serviceCode: string,
  quotaCode: string,
  desiredValue: number,
  requestedBy: string,
  requestedAt: string,
  requestId?: string | null,
  caseId?: string | null,
  status: string,
  updatedAt?: string | null,
}

export interface CapacityAccount {
  awsAccountId: string,
  region: string,
  programs: { projectId: string, projectName?: string | null, stages: string[] }[],
  collectedAt: string,
  error?: string | null,
  quotas: CapacityQuota[],
  instances: CapacityInstance[],
  gp3GiB: number,
  requests: QuotaIncreaseRequest[],
}

export interface CapacityOverview {
  collectedAt: string | null,
  alarmUsedPercent: number,
  totals: {
    accounts: number,
    programs: number,
    workbenchesByState: Record<string, number>,
    runningByInstanceType: Record<string, number>,
    gpuInstancesRunning: number,
    gp3GiB: number,
  },
  accounts: CapacityAccount[],
}

export interface ProjectCapacity {
  projectId: string,
  accounts: {
    awsAccountId: string,
    region: string,
    collectedAt: string,
    available: Record<string, number | null>,
    quotas: CapacityQuota[],
  }[],
}

export interface CapacityWorkbench {
  provisionedProductId: string,
  owner: string,
  productName: string,
  versionName: string,
  stage: string,
  awsAccountId: string,
  region: string,
  status: string,
  instanceType?: string | null,
  volumeSize?: string | null,
  startDate?: string | null,
  lastUpdateDate?: string | null,
  idleTimeoutMinutes?: number | null,
  nightlyStopDisabled?: boolean,
}

export interface ProjectCapacityWorkbenches {
  projectId: string,
  workbenches: CapacityWorkbench[],
  runningByInstanceType: Record<string, number>,
}

const CONTENT_TYPE = 'Content-Type';

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const config = Amplify.getConfig();
  const basePath = config.API?.REST?.[PROVISIONING_API_NAME]?.endpoint || '';
  const access = await getAccessToken();
  const headers = new Headers({ Authorization: `Bearer ${access}` });
  if (body !== undefined) {
    headers.set(CONTENT_TYPE, 'application/json');
  }
  const response = await fetch(`${basePath}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const json = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(json.message || `${response.status} ${response.statusText}`);
  }
  return json as T;
}

export const capacityAPI = {
  getOverview: () => call<CapacityOverview>('GET', '/capacity'),
  getProjectCapacity: (projectId: string, instanceTypes: string[]) =>
    call<ProjectCapacity>(
      'GET',
      `/projects/${encodeURIComponent(projectId)}/capacity` +
      `?instanceTypes=${encodeURIComponent(instanceTypes.join(','))}`
    ),
  getProjectWorkbenches: (projectId: string) =>
    call<ProjectCapacityWorkbenches>(
      'GET', `/projects/${encodeURIComponent(projectId)}/capacity/workbenches`
    ),
  requestIncrease: (awsAccountId: string, quotaCode: string, desiredValue: number, region: string) =>
    call<QuotaIncreaseRequest>(
      'PUT',
      `/capacity/accounts/${encodeURIComponent(awsAccountId)}` +
      `/quotas/${encodeURIComponent(quotaCode)}/increase`,
      { desiredValue, region }
    ),
};

/** The launch form's hint for one size: null = no data (the launch is not refused on it). */
export function capacityHint(capacity: ProjectCapacity | undefined, instanceType: string):
{ available: number | null, collectedAt?: string } {
  const account = capacity?.accounts?.[0];
  if (!account) {
    return { available: null };
  }
  const value = account.available?.[instanceType];
  return { available: value === undefined ? null : value, collectedAt: account.collectedAt };
}
