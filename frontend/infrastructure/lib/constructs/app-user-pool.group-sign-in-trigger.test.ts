import { mockClient } from 'aws-sdk-client-mock';
import { InvokeCommand, LambdaClient } from '@aws-sdk/client-lambda';
import { PreTokenGenerationTriggerEvent } from 'aws-lambda';
import { handler } from './app-user-pool.group-sign-in-trigger';

const client = mockClient(LambdaClient);

beforeEach(() => {
  client.reset();
  process.env.GROUP_MEMBERSHIP_FUNCTION_NAME = 'membership';
});

function event(triggerSource: string): PreTokenGenerationTriggerEvent {
  return { triggerSource, userPoolId: 'pool', userName: 'user' } as PreTokenGenerationTriggerEvent;
}

test.each(['TokenGeneration_HostedAuth', 'TokenGeneration_Authentication'])(
  'queues membership reconciliation for %s', async source => {
    client.on(InvokeCommand).resolves({ StatusCode: 202 });
    const input = event(source);
    expect(await handler(input, {} as never, () => undefined)).toBe(input);
    const request = client.commandCalls(InvokeCommand)[0].args[0].input;
    expect(request.FunctionName).toBe('membership');
    expect(request.InvocationType).toBe('Event');
    expect(JSON.parse(Buffer.from(request.Payload as Uint8Array).toString())).toEqual({
      triggerSource: source, userPoolId: 'pool', userName: 'user',
    });
  }
);

test('does not recreate membership on refresh', async () => {
  await handler(event('TokenGeneration_RefreshTokens'), {} as never, () => undefined);
  expect(client.calls()).toHaveLength(0);
});

test('does not hide queueing failures', async () => {
  client.on(InvokeCommand).rejects(new Error('unavailable'));
  await expect(handler(event('TokenGeneration_HostedAuth'), {} as never, () => undefined))
    .rejects.toThrow('unavailable');
});
