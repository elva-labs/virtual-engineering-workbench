import { Amplify } from 'aws-amplify';
import { getAccessToken } from '../api-auth';

export interface ProjectWorkbenchLifecycle {
  alwaysOn: boolean,
  idleStopMinutes: number | null,
  nightlyStop: boolean | null,
  weekendStop: boolean | null,
  allowUserDisableNightlyStop: boolean,
  allowUserIdleTimeout: boolean,
  userIdleTimeoutMinMinutes: number,
  userIdleTimeoutMaxMinutes: number,
}

export interface ProjectWorkbenchLifecycleResponse {
  projectId: string,
  settings: ProjectWorkbenchLifecycle | null,
  platformDefaults: {
    idleStopMinutes: number,
    nightlyStop: boolean,
    nightlyStopTime: string,
    timezone: string,
    weekendStop: boolean,
  },
  deployment: {
    idleStopEnabled: boolean,
    nightlyStopScheduleEnabled: boolean,
  },
  canEdit: boolean,
  managedBy?: string | null,
  managedSource?: string | null,
}

const CACHE_CONTROL_HEADER = 'Cache-Control';
const CONTENT_TYPE_HEADER = 'Content-Type';

async function request(
  projectId: string,
  method: 'GET' | 'PUT' | 'DELETE',
  settings?: ProjectWorkbenchLifecycle,
): Promise<ProjectWorkbenchLifecycleResponse> {
  const access = await getAccessToken();
  const endpoint = Amplify.getConfig().API?.REST?.ProjectsAPI?.endpoint;
  if (!endpoint) {
    throw new Error('Projects API is not configured.');
  }
  const response = await fetch(
    `${endpoint.replace(/\/$/u, '')}/projects/${encodeURIComponent(projectId)}/workbench-lifecycle`,
    {
      method,
      headers: {
        Authorization: `Bearer ${access}`,
        [CACHE_CONTROL_HEADER]: 'max-age=0, no-cache',
        ...settings ? { [CONTENT_TYPE_HEADER]: 'application/json' } : {},
      },
      ...settings ? { body: JSON.stringify(settings) } : {},
    },
  );
  if (!response.ok) {
    const operation = method === 'GET' ? 'load' : 'update';
    const error = new Error(`Unable to ${operation} shutdown policy (${response.status}).`);
    throw Object.assign(error, { response });
  }
  const result: ProjectWorkbenchLifecycleResponse = await response.json();
  if (result.projectId !== projectId) {
    throw new Error('The shutdown policy response belongs to a different program. Please retry.');
  }
  return result;
}

export const projectWorkbenchLifecycleAPI = {
  get: (projectId: string) => request(projectId, 'GET'),
  update: (projectId: string, settings: ProjectWorkbenchLifecycle) => request(projectId, 'PUT', settings),
  reset: (projectId: string) => request(projectId, 'DELETE'),
};
