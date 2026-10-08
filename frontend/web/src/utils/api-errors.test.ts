/* eslint-disable @typescript-eslint/no-magic-numbers, @typescript-eslint/naming-convention */
import { describe, expect, it } from 'vitest';
import { describeApiError, isPermissionError, permissionMessage } from './api-errors';

class FakeResponseError extends Error {
  constructor(public response: Response) {
    super('Response returned an error code');
  }
}

function failed(status: number, body?: unknown, headers: Record<string, string> = {}) {
  return new FakeResponseError(new Response(body === undefined ? null : JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', ...headers },
  }));
}

const REFUSED = 'You don\'t have permission to change the standard workbench settings in proj-12345. ' +
  'It needs Program owner or Admin.';

describe('describeApiError', () => {
  it('says what is missing when the authorizer refuses', async () => {
    const e = failed(403, { Message: 'User is not authorized to access this resource' });
    const context = { action: 'change the standard workbench settings', program: 'proj-12345' };

    expect(await describeApiError(e, context)).toBe(REFUSED);
    expect(isPermissionError(e)).toBe(true);
  });

  it('treats 401 like 403 and has defaults without context', async () => {
    expect(await describeApiError(failed(401))).toBe(permissionMessage());
    expect(permissionMessage()).toContain('do this in this program');
  });

  it('keeps the backend\'s own message', async () => {
    const e = failed(400, { message: 'Unknown size m9.huge' });

    expect(await describeApiError(e)).toBe('Unknown size m9.huge');
  });

  it('names an input check rejection instead of the generic client text', async () => {
    const e = failed(400, { message: 'Invalid request body' }, { 'x-amzn-requestid': 'req-1' });

    expect(await describeApiError(e))
      .toBe('The API rejected the request as invalid (status 400). (reference req-1)');
  });

  it('keeps a generic message with the reference for server errors', async () => {
    const e = failed(502, { message: 'Internal server error' }, { 'x-amzn-requestid': 'req-2' });
    const text = await describeApiError(e);

    expect(text).toContain('Something went wrong on the server (status 502)');
    expect(text).toContain('reference req-2');
    expect(isPermissionError(failed(502))).toBe(false);
  });

  it('explains an unreachable API', async () => {
    expect(await describeApiError(new TypeError('Failed to fetch'))).toContain('could not be reached');
  });
});
