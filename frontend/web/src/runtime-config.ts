// The deployment's runtime configuration (Cognito and the REST APIs), served next to the app as
// /aws-exports.json (configure_auth.sh writes it into public/, the build copies it into dist/), so
// the bundle does not depend on the environment. index.tsx loads it before any other module runs.

// Amplify's configuration names.
/* eslint @typescript-eslint/naming-convention: "off" */
export interface AwsExports {
  Auth: {
    Cognito: {
      userPoolId: string,
      userPoolClientId: string,
      loginWith: {
        oauth: {
          domain: string,
          scopes: string[],
          redirectSignIn: string[],
          redirectSignOut: string[],
          responseType: 'code',
        },
      },
    },
    cookieStorage: { expires: number },
  },
  API: {
    REST: { [name: string]: { endpoint: string, region: string } },
  },
}

export const RUNTIME_CONFIG_PATH = '/aws-exports.json';

let loaded: AwsExports | undefined;

export async function loadRuntimeConfig(): Promise<void> {
  const response = await fetch(RUNTIME_CONFIG_PATH, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`${RUNTIME_CONFIG_PATH}: ${response.status}`);
  }
  loaded = await response.json() as AwsExports;
}

export function runtimeConfig(): AwsExports {
  if (!loaded) {
    throw new Error(`${RUNTIME_CONFIG_PATH} is not loaded yet.`);
  }
  return loaded;
}
