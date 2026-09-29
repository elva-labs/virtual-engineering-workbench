import { AppConfig } from '../utils/app-config';
import { getAccessToken } from './api-auth';

const HTTPS_DEFAULT_PORT = 443;
const MIN_PORT_NUMBER = 1;
const MAX_PORT_NUMBER = 65535;

export interface DirectDcvConnectionDetails {
  mode: 'direct',
}

export interface GatewayDcvConnectionDetails {
  mode: 'gateway',
  gatewayUrl: string,
  sessionId: string,
  token: string,
}

export type DcvConnectionDetails = DirectDcvConnectionDetails | GatewayDcvConnectionDetails;

export interface GatewayAddress {
  host: string,
  port: number,
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim() || /[\r\n\0]/u.test(value)) {
    throw new Error(`Invalid DCV connection details: ${field}`);
  }
  return value;
}

function hasInvalidGatewayTransport(parsed: URL): boolean {
  return parsed.protocol !== 'https:' || !parsed.hostname ||
    parsed.username !== '' || parsed.password !== '';
}

function hasInvalidGatewayPath(parsed: URL): boolean {
  return parsed.pathname !== '' && parsed.pathname !== '/' || parsed.search !== '' || parsed.hash !== '';
}

export function parseGatewayAddress(gatewayUrl: string): GatewayAddress {
  let parsed: URL;
  try {
    parsed = new URL(gatewayUrl);
  } catch {
    throw new Error('Invalid DCV connection details: gatewayUrl');
  }

  if (hasInvalidGatewayTransport(parsed) || hasInvalidGatewayPath(parsed)) {
    throw new Error('Invalid DCV connection details: gatewayUrl');
  }

  const port = parsed.port ? Number(parsed.port) : HTTPS_DEFAULT_PORT;
  if (!Number.isInteger(port) || port < MIN_PORT_NUMBER || port > MAX_PORT_NUMBER) {
    throw new Error('Invalid DCV connection details: gatewayUrl');
  }

  return { host: parsed.hostname, port };
}

function parseConnectionDetails(value: unknown, expectedGatewayOrigin: string): DcvConnectionDetails {
  if (!value || typeof value !== 'object') {
    throw new Error('Invalid DCV connection details response');
  }

  const details = value as Record<string, unknown>;
  if (details.mode === 'direct') {
    return { mode: 'direct' };
  }
  if (details.mode !== 'gateway') {
    throw new Error('Invalid DCV connection details response');
  }

  const gatewayUrl = requireString(details.gatewayUrl, 'gatewayUrl');
  const sessionId = requireString(details.sessionId, 'sessionId');
  const token = requireString(details.token, 'token');
  parseGatewayAddress(gatewayUrl);
  if (new URL(gatewayUrl).origin !== expectedGatewayOrigin) {
    throw new Error('Invalid DCV connection details: gateway origin mismatch');
  }

  return { mode: 'gateway', gatewayUrl, sessionId, token };
}

export function isDcvGatewayConfigured(): boolean {
  return Boolean(AppConfig.DcvGatewayUrl?.trim());
}

export async function getDcvConnectionDetails(
  projectId: string,
  provisionedProductId: string,
): Promise<DcvConnectionDetails> {
  const gatewayUrl = AppConfig.DcvGatewayUrl?.trim();
  if (!gatewayUrl) {
    return { mode: 'direct' };
  }

  const gatewayAddress = parseGatewayAddress(gatewayUrl);
  const gatewayOrigin = new URL(`https://${gatewayAddress.host}:${gatewayAddress.port}`).origin;
  const apiUrl = new URL('/api/dcv/connections', gatewayOrigin).toString();
  const accessToken = await getAccessToken();
  if (!accessToken) {
    throw new Error('Unable to authenticate the DCV connection details request');
  }

  const response = await fetch(apiUrl, {
    method: 'POST',
    headers: new Headers([
      ['Authorization', `Bearer ${accessToken}`],
      ['Content-Type', 'application/json'],
    ]),
    body: JSON.stringify({ projectId, provisionedProductId }),
    cache: 'no-store',
    redirect: 'error',
  });
  if (!response.ok) {
    throw new Error(`DCV connection details request failed (${response.status})`);
  }

  return parseConnectionDetails(await response.json(), gatewayOrigin);
}
