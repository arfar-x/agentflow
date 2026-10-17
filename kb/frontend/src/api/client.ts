// One place that talks to the console's API.
//
// Every call sends the console's own header: it is what the server checks
// (FR-ADM-03) to be sure a request came from this page and not from another
// site making the operator's browser send one. The session itself is an
// HttpOnly cookie the browser attaches; nothing here ever sees the token again
// after login.

export class ApiError extends Error {
  readonly status: number;
  readonly kind: string;
  readonly detail: Record<string, unknown>;

  constructor(status: number, kind: string, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
    this.detail = detail;
  }
}

type Json = Record<string, unknown> | unknown[];

let onUnauthorized: (() => void) | null = null;

/** The app registers what happens when the session ends under it (show the login). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

export async function request<T>(method: string, path: string, body?: Json): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: {
        'X-KB-Admin': '1',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'network', 'Could not reach the console. Is kb-admin running?');
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const error = (payload as { error?: { type?: string; message?: string } } | null)?.error;
    // A login that fails is a 401 too, but it is the form's to report; only a
    // session that ends under a screen the person was already using sends
    // them back to the login.
    if (response.status === 401 && path !== '/api/session') onUnauthorized?.();
    throw new ApiError(
      response.status,
      error?.type ?? 'error',
      error?.message ?? `The console answered ${response.status}`,
      (error as Record<string, unknown> | undefined) ?? {},
    );
  }
  return payload as T;
}

export const get = <T>(path: string) => request<T>('GET', path);
export const post = <T>(path: string, body: Json = {}) => request<T>('POST', path, body);
export const del = <T>(path: string) => request<T>('DELETE', path);
