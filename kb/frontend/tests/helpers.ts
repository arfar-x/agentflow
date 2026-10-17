import { vi } from 'vitest';

export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call) => { status?: number; body?: unknown } | undefined;

/** A fetch that answers from a table of handlers and records every call. */
export function mockFetch(routes: Record<string, Handler | { status?: number; body?: unknown }>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    const call: Call = {
      method,
      path,
      headers: Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>)),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const key = `${method} ${path.split('?')[0]}`;
    const route = routes[key];
    const answer = typeof route === 'function' ? route(call) : route;
    if (!answer) return new Response(JSON.stringify({ error: { type: 'not_found', message: `no route ${key}` } }), { status: 404 });
    return new Response(JSON.stringify(answer.body ?? {}), { status: answer.status ?? 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return { calls, fetchMock };
}
