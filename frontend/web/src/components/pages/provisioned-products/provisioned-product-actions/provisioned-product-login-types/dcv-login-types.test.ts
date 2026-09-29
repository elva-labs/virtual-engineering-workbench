import { describe, expect, it } from 'vitest';
import { DCVBrowserLoginType } from './dcv-browser-login-type';
import { DCVFileLoginType } from './dcv-file-login-type';
import { LoginRequest } from './interface';

const HTTPS_DEFAULT_PORT = 443;

const loginRequest = {
  user: { userId: 'user@example.com' },
  userDomain: 'EXAMPLE',
  provisionedProduct: {
    projectId: 'project-1',
    provisionedProductId: 'workbench-1',
    productName: 'Workbench',
    stage: 'dev',
    versionName: '1.0',
  },
  connectAddress: 'dcv.example.com',
  vpnConnection: true,
  extendToAllMonitors: true,
} as LoginRequest;

describe('DCV gateway login builders', () => {
  it('builds a browser URL with encoded gateway token and session ID', async () => {
    const result = await new DCVBrowserLoginType().doLogin(loginRequest, {
      sessionId: 'session / one',
      authToken: 'opaque + token',
      portOverride: HTTPS_DEFAULT_PORT,
    });

    expect(result.loginUrl).toBe(
      'https://dcv.example.com/?authToken=opaque%20%2B%20token#session%20%2F%20one'
    );
  });

  it('builds a DCV file targeting the gateway host and returned session credentials', async () => {
    const result = await new DCVFileLoginType().doLogin(loginRequest, {
      sessionId: 'gateway-session',
      authToken: 'single-use-token',
      portOverride: HTTPS_DEFAULT_PORT,
    });

    expect(result.loginFile?.loginFileContent).toContain('host=dcv.example.com\n');
    expect(result.loginFile?.loginFileContent).toContain('port=443\n');
    expect(result.loginFile?.loginFileContent).not.toContain('user=');
    expect(result.loginFile?.loginFileContent).toContain('sessionid=gateway-session\n');
    expect(result.loginFile?.loginFileContent).toContain('authtoken=single-use-token\n');
    expect(result.loginFile?.loginFileContent).not.toContain('cognito-access-token');
  });

  it('preserves direct browser behavior when no gateway context is supplied', async () => {
    const result = await new DCVBrowserLoginType().doLogin({
      ...loginRequest,
      connectAddress: '192.0.2.10',
    });

    expect(result.loginUrl).toBe('https://192.0.2.10:8443/#console');
  });

  it('preserves the direct DCV file username when there is no gateway token', async () => {
    const result = await new DCVFileLoginType().doLogin(loginRequest);

    expect(result.loginFile?.loginFileContent).toContain('user=example\\user@example.com\n');
    expect(result.loginFile?.loginFileContent).toContain('sessionid=console\n');
  });
});
