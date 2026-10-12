/* eslint-disable @typescript-eslint/no-magic-numbers, @stylistic/max-len */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Amplify } from 'aws-amplify';
import { getAccessToken } from '../api-auth';
import { projectWorkbenchLifecycleAPI } from './project-workbench-lifecycle-api';
import { inheritedPolicy } from '../../components/pages/projects/project-details/project-shutdown-policy.logic';

vi.mock('aws-amplify', () => ({ Amplify: { getConfig: vi.fn() } }));
vi.mock('../api-auth', () => ({ getAccessToken: vi.fn() }));

const CACHE_CONTROL_HEADER = 'Cache-Control';
const CONTENT_TYPE_HEADER = 'Content-Type';
const fetchMock = vi.fn();
const result = { projectId: 'project/1', settings: null };

beforeEach(() => {
  vi.mocked(getAccessToken).mockResolvedValue('token');
  vi.mocked(Amplify.getConfig).mockReturnValue({ API: { REST: { ProjectsAPI: { endpoint: 'https://example.com/projects-api/' } } } });
  fetchMock.mockReset().mockResolvedValue({ ok: true, json: () => Promise.resolve(result) });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('project workbench lifecycle API', () => {
  it('authenticates and encodes the project path when loading an uncached response', async() => {
    expect(await projectWorkbenchLifecycleAPI.get('project/1')).toEqual(result);
    expect(fetchMock).toHaveBeenCalledWith('https://example.com/projects-api/projects/project%2F1/workbench-lifecycle', {
      method: 'GET',
      headers: { Authorization: 'Bearer token', [CACHE_CONTROL_HEADER]: 'max-age=0, no-cache' },
    });
  });

  it('puts the policy model directly and preserves inherited nulls', async() => {
    await projectWorkbenchLifecycleAPI.update('project/1', inheritedPolicy);
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), {
      method: 'PUT',
      headers: { Authorization: 'Bearer token', [CACHE_CONTROL_HEADER]: 'max-age=0, no-cache', [CONTENT_TYPE_HEADER]: 'application/json' },
      body: JSON.stringify(inheritedPolicy),
    });
  });

  it('deletes the explicit policy without sending a body', async() => {
    await projectWorkbenchLifecycleAPI.reset('project/1');
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), {
      method: 'DELETE',
      headers: { Authorization: 'Bearer token', [CACHE_CONTROL_HEADER]: 'max-age=0, no-cache' },
    });
  });

  it('preserves an error response for the portal error helper', async() => {
    const response = { ok: false, status: 403 };
    fetchMock.mockResolvedValue(response);
    await expect(projectWorkbenchLifecycleAPI.update('project/1', inheritedPolicy)).rejects.toMatchObject({ response });
  });

  it('rejects a response belonging to a different program', async() => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve({ projectId: 'wrong-project' }) });
    await expect(projectWorkbenchLifecycleAPI.get('project/1')).rejects.toThrow('different program');
  });

  it('refuses a missing API endpoint before sending a request', async() => {
    vi.mocked(Amplify.getConfig).mockReturnValue({});
    await expect(projectWorkbenchLifecycleAPI.get('project/1')).rejects.toThrow('not configured');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('propagates authentication failures without sending a request', async() => {
    vi.mocked(getAccessToken).mockRejectedValueOnce(new Error('not signed in'));
    await expect(projectWorkbenchLifecycleAPI.get('project/1')).rejects.toThrow('not signed in');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
