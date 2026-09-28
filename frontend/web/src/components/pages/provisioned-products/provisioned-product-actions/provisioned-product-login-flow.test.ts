import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WORKBENCH_CONNECTION_TYPE } from '../../workbenches/workbenches.static';
import { DCVBrowserLoginType, DCVFileLoginType } from './provisioned-product-login-types';
import { LoginRequest } from './provisioned-product-login-types/interface';
import { ProvisionedProductLoginType } from './provisioned-product-login-types/login-type';
import { performProvisionedProductLogin } from './provisioned-product-login-flow';

const ONE_REQUEST = 1;
const HTTPS_DEFAULT_PORT = 443;

const gatewayClient = vi.hoisted(() => ({
  configured: true,
  getDetails: vi.fn(),
}));

vi.mock('../../../../services/dcv-gateway-api', () => ({
  isDcvGatewayConfigured: () => gatewayClient.configured,
  getDcvConnectionDetails: gatewayClient.getDetails,
  parseGatewayAddress: (gatewayUrl: string) => {
    const url = new URL(gatewayUrl);
    return { host: url.hostname, port: url.port ? Number(url.port) : HTTPS_DEFAULT_PORT };
  },
}));

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
  connectAddress: '192.0.2.10',
  vpnConnection: true,
  extendToAllMonitors: true,
} as LoginRequest;

describe('provisioned product login flow', () => {
  beforeEach(() => {
    gatewayClient.configured = true;
    gatewayClient.getDetails.mockReset();
  });

  it('opens DCV Browser through the gateway with one details request and no second login', async () => {
    gatewayClient.getDetails.mockResolvedValue({
      mode: 'gateway',
      gatewayUrl: 'https://dcv.example.com:8443',
      sessionId: 'session / one',
      token: 'single-use-token',
    });
    const authorize = vi.fn().mockResolvedValue(undefined);

    const result = await performProvisionedProductLogin({
      connectionOption: WORKBENCH_CONNECTION_TYPE.DcvBrowser,
      loginType: new DCVBrowserLoginType(),
      loginRequest,
      authorizeDirectConnection: authorize,
    });

    expect(gatewayClient.getDetails).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(gatewayClient.getDetails).toHaveBeenCalledWith('project-1', 'workbench-1');
    expect(authorize).not.toHaveBeenCalled();
    expect(result.loginUrl).toBe(
      'https://dcv.example.com:8443/?authToken=single-use-token#session%20%2F%20one'
    );
  });

  it('creates a gateway DCV file without prompting for an AD user', async () => {
    gatewayClient.getDetails.mockResolvedValue({
      mode: 'gateway',
      gatewayUrl: 'https://dcv.example.com',
      sessionId: 'gateway-session',
      token: 'single-use-token',
    });

    const result = await performProvisionedProductLogin({
      connectionOption: WORKBENCH_CONNECTION_TYPE.DcvFile,
      loginType: new DCVFileLoginType(),
      loginRequest,
      authorizeDirectConnection: vi.fn().mockResolvedValue(undefined),
    });

    expect(gatewayClient.getDetails).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(result.loginFile?.loginFileContent).toContain('host=dcv.example.com\n');
    expect(result.loginFile?.loginFileContent).not.toContain('user=');
    expect(result.loginFile?.loginFileContent).toContain('sessionid=gateway-session\n');
    expect(result.loginFile?.loginFileContent).toContain('authtoken=single-use-token\n');
  });

  it('keeps using the old workbench address when the API explicitly returns direct mode', async () => {
    gatewayClient.getDetails.mockResolvedValue({ mode: 'direct' });
    const authorize = vi.fn().mockResolvedValue(undefined);

    const result = await performProvisionedProductLogin({
      connectionOption: WORKBENCH_CONNECTION_TYPE.DcvBrowser,
      loginType: new DCVBrowserLoginType(),
      loginRequest,
      authorizeDirectConnection: authorize,
    });

    expect(gatewayClient.getDetails).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(authorize).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(result.loginUrl).toBe('https://192.0.2.10:8443/#console');
  });

  it('does not request gateway details when gateway configuration is unset', async () => {
    gatewayClient.configured = false;
    const authorize = vi.fn().mockResolvedValue(undefined);

    const result = await performProvisionedProductLogin({
      connectionOption: WORKBENCH_CONNECTION_TYPE.DcvBrowser,
      loginType: new DCVBrowserLoginType(),
      loginRequest,
      authorizeDirectConnection: authorize,
    });

    expect(gatewayClient.getDetails).not.toHaveBeenCalled();
    expect(authorize).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(result.loginUrl).toBe('https://192.0.2.10:8443/#console');
  });

  it('fails closed on API failure before invoking a builder or authorizing direct IP', async () => {
    gatewayClient.getDetails.mockRejectedValue(new Error('gateway unavailable'));
    const authorize = vi.fn().mockResolvedValue(undefined);
    const loginType = { doLogin: vi.fn() } as unknown as ProvisionedProductLoginType;

    await expect(performProvisionedProductLogin({
      connectionOption: WORKBENCH_CONNECTION_TYPE.DcvBrowser,
      loginType,
      loginRequest,
      authorizeDirectConnection: authorize,
    })).rejects.toThrow('gateway unavailable');

    expect(gatewayClient.getDetails).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(authorize).not.toHaveBeenCalled();
    expect(loginType.doLogin).not.toHaveBeenCalled();
  });

  it('preserves non-DCV login flow without requesting gateway details', async () => {
    const authorize = vi.fn().mockResolvedValue(undefined);
    const loginType = {
      doLogin: vi.fn().mockResolvedValue({ type: 'browser', loginUrl: 'ssh://example' }),
    } as unknown as ProvisionedProductLoginType;

    await performProvisionedProductLogin({
      connectionOption: WORKBENCH_CONNECTION_TYPE.SSH,
      loginType,
      loginRequest,
      authorizeDirectConnection: authorize,
    });

    expect(gatewayClient.getDetails).not.toHaveBeenCalled();
    expect(authorize).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(loginType.doLogin).toHaveBeenCalledTimes(ONE_REQUEST);
  });
});
