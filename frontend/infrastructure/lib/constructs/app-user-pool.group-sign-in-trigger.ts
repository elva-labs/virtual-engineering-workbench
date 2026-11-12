import { PreTokenGenerationTriggerHandler } from 'aws-lambda';
import { InvokeCommand, LambdaClient } from '@aws-sdk/client-lambda';

const client = new LambdaClient({});

export const handler: PreTokenGenerationTriggerHandler = async event => {
  if (!['TokenGeneration_HostedAuth', 'TokenGeneration_Authentication'].includes(event.triggerSource)) {
    return event;
  }
  // Queue once per sign-in, not on token refresh. Membership changes can span
  // many projects and must not run inside Cognito's five-second trigger limit.
  await client.send(new InvokeCommand({
    FunctionName: process.env.GROUP_MEMBERSHIP_FUNCTION_NAME,
    InvocationType: 'Event',
    Payload: Buffer.from(JSON.stringify({
      userPoolId: event.userPoolId,
      userName: event.userName,
      triggerSource: event.triggerSource,
    })),
  }));
  return event;
};
