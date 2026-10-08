// What a failed API call tells the user. A refusal (401/403, from the API's authorizer) says
// what's missing instead of the generated client's "Response returned an error code"; server and network
// failures keep a generic message, with the request id when the browser can read it.

export type ApiErrorContext = {
  // What the user tried, e.g. "change the standard workbench settings".
  action?: string,
  // The program's name.
  program?: string,
};

const UNAUTHORIZED = 401;
const FORBIDDEN = 403;
const BAD_REQUEST = 400;
const SERVER_ERROR = 500;

export function permissionMessage({ action, program }: ApiErrorContext = {}): string {
  return `You don't have permission to ${action || 'do this'} in ${program || 'this program'}. ` +
    'It needs Program owner or Admin.';
}

type WithResponse = { response?: unknown };

function responseOf(e: unknown): Response | undefined {
  const response = (e as WithResponse | null)?.response;
  return response instanceof Response ? response : undefined;
}

function requestId(response: Response): string | undefined {
  return response.headers.get('x-amzn-requestid') || response.headers.get('x-amz-apigw-id') || undefined;
}

async function bodyMessage(response: Response): Promise<string | undefined> {
  try {
    const body = await response.clone().json();
    const message = body?.message ?? body?.Message;
    const detail = body?.validationError;
    return [message, detail].filter(Boolean).join(' ') || undefined;
  } catch {
    return undefined;
  }
}

function withReference(text: string, response: Response): string {
  const id = requestId(response);
  return id ? `${text} (reference ${id})` : text;
}

export async function describeApiError(e: unknown, context: ApiErrorContext = {}): Promise<string> {
  const response = responseOf(e);
  if (!response) {
    // fetch rejects with a TypeError when the API can't be reached at all.
    if (e instanceof TypeError) {
      return 'The VEW API could not be reached. Check your connection and try again.';
    }
    return e instanceof Error ? e.message : String(e);
  }
  if (response.status === UNAUTHORIZED || response.status === FORBIDDEN) {
    return permissionMessage(context);
  }
  if (response.status >= SERVER_ERROR) {
    const text = `Something went wrong on the server (status ${response.status}). Try again in a moment.`;
    return withReference(text, response);
  }
  const message = await bodyMessage(response);
  if (response.status === BAD_REQUEST && (!message || message === 'Invalid request body')) {
    return withReference('The API rejected the request as invalid (status 400).', response);
  }
  return message || withReference(`The request failed (status ${response.status}).`, response);
}

export function isPermissionError(e: unknown): boolean {
  const status = responseOf(e)?.status;
  return status === UNAUTHORIZED || status === FORBIDDEN;
}
