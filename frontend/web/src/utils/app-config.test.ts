import { describe, expect, it } from 'vitest';
import { AppConfig } from './app-config';

describe('AppConfig', () => {
  it('exposes the build-time DCV gateway URL when configured', () => {
    expect(AppConfig.DcvGatewayUrl).toBe(process.env.DCV_GATEWAY_URL);
  });
});
