import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppConfig } from '../utils/app-config';
import { getAccessToken } from './api-auth';
import { getDcvConnectionDetails, parseGatewayAddress } from './dcv-gateway-api';

vi.mock('./api-auth', () => ({
  getAccessToken: vi.fn(),
}));

const ONE_REQUEST = 1;
const GATEWAY_PORT = 8443;
const SERVICE_UNAVAILABLE_STATUS = 503;
const GATEWAY_CONFIG_KEY = 'DcvGatewayUrl';
const mutableConfig = AppConfig as unknown as Record<string, string | undefined>;
const originalGatewayUrl = mutableConfig[GATEWAY_CONFIG_KEY];

function successfulResponse(body: unknown): Response {
  return {
    ok: true,
    json: () => Promise.resolve(body),
  } as Response;
}

describe('DCV gateway connection details', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', vi.fn());
    (getAccessToken as ReturnType<typeof vi.fn>).mockResolvedValue('cognito-access-token');
  });

  afterEach(() => {
    mutableConfig[GATEWAY_CONFIG_KEY] = originalGatewayUrl;
    vi.unstubAllGlobals();
  });

  it('uses direct mode without a gateway request when no gateway is configured', async () => {
    mutableConfig[GATEWAY_CONFIG_KEY] = undefined;

    await expect(getDcvConnectionDetails('project-1', 'workbench-1')).resolves.toEqual({ mode: 'direct' });
    expect(fetch).not.toHaveBeenCalled();
    expect(getAccessToken).not.toHaveBeenCalled();
  });

  it('makes one Cognito-authenticated request and validates gateway details', async () => {
    mutableConfig[GATEWAY_CONFIG_KEY] = `https://dcv.example.com:${GATEWAY_PORT}/`;
    vi.mocked(fetch).mockResolvedValue(successfulResponse({
      mode: 'gateway',
      gatewayUrl: `https://dcv.example.com:${GATEWAY_PORT}`,
      sessionId: 'session / one',
      token: 'opaque-token',
    }));

    await expect(getDcvConnectionDetails('project-1', 'workbench-1')).resolves.toEqual({
      mode: 'gateway',
      gatewayUrl: `https://dcv.example.com:${GATEWAY_PORT}`,
      sessionId: 'session / one',
      token: 'opaque-token',
    });
    expect(fetch).toHaveBeenCalledTimes(ONE_REQUEST);
    expect(fetch).toHaveBeenCalledWith(
      `https://dcv.example.com:${GATEWAY_PORT}/api/dcv/connections`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ projectId: 'project-1', provisionedProductId: 'workbench-1' }),
        cache: 'no-store',
        redirect: 'error',
      })
    );
    const requestHeaders = new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers);
    expect(requestHeaders.get('Authorization')).toBe('Bearer cognito-access-token');
    expect(requestHeaders.get('Content-Type')).toBe('application/json');
    expect(parseGatewayAddress(`https://dcv.example.com:${GATEWAY_PORT}`)).toEqual({
      host: 'dcv.example.com',
      port: GATEWAY_PORT,
    });
  });

  it('returns explicit direct mode for products that are not gateway enabled', async () => {
    mutableConfig[GATEWAY_CONFIG_KEY] = 'https://dcv.example.com';
    vi.mocked(fetch).mockResolvedValue(successfulResponse({ mode: 'direct' }));

    await expect(getDcvConnectionDetails('project-1', 'workbench-1')).resolves.toEqual({ mode: 'direct' });
    expect(fetch).toHaveBeenCalledTimes(ONE_REQUEST);
  });

  it('fails closed when the connection-details API fails', async () => {
    mutableConfig[GATEWAY_CONFIG_KEY] = 'https://dcv.example.com';
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: SERVICE_UNAVAILABLE_STATUS } as Response);

    await expect(getDcvConnectionDetails('project-1', 'workbench-1'))
      .rejects.toThrow(`DCV connection details request failed (${SERVICE_UNAVAILABLE_STATUS})`);
    expect(fetch).toHaveBeenCalledTimes(ONE_REQUEST);
  });

  it('rejects malformed gateway endpoints and incomplete gateway responses', async () => {
    mutableConfig[GATEWAY_CONFIG_KEY] = 'https://dcv.example.com';
    vi.mocked(fetch).mockResolvedValue(successfulResponse({
      mode: 'gateway',
      gatewayUrl: 'http://untrusted.example.com',
      sessionId: 's',
      token: 't',
    }));

    await expect(getDcvConnectionDetails('project-1', 'workbench-1'))
      .rejects.toThrow('Invalid DCV connection details: gatewayUrl');
    expect(fetch).toHaveBeenCalledTimes(ONE_REQUEST);
  });

  it('rejects a gateway origin that differs from the configured origin', async () => {
    mutableConfig[GATEWAY_CONFIG_KEY] = 'https://dcv.example.com';
    vi.mocked(fetch).mockResolvedValue(successfulResponse({
      mode: 'gateway',
      gatewayUrl: 'https://attacker.example.com',
      sessionId: 'session-1',
      token: 'single-use-token',
    }));

    await expect(getDcvConnectionDetails('project-1', 'workbench-1'))
      .rejects.toThrow('Invalid DCV connection details: gateway origin mismatch');
    expect(fetch).toHaveBeenCalledTimes(ONE_REQUEST);
  });
});
