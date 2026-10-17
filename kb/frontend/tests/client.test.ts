import { ApiError, request, setUnauthorizedHandler } from '../src/api/client';
import { mockFetch } from './helpers';

afterEach(() => {
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe('the API client', () => {
  it('sends the console header on every call, and the session cookie by the browser', async () => {
    const { calls, fetchMock } = mockFetch({ 'GET /api/overview': { body: { ok: true } } });
    await request('GET', '/api/overview');
    expect(calls[0]!.headers['X-KB-Admin']).toBe('1');
    expect(fetchMock.mock.calls[0]![1]).toMatchObject({ credentials: 'same-origin' });
  });

  it('sends a JSON body with its content type', async () => {
    const { calls } = mockFetch({ 'POST /api/sync': { status: 202, body: { request_id: 4 } } });
    await expect(request('POST', '/api/sync', { source_id: '*' })).resolves.toEqual({ request_id: 4 });
    expect(calls[0]!.headers['Content-Type']).toBe('application/json');
    expect(calls[0]!.body).toEqual({ source_id: '*' });
  });

  it("turns the server's error into one the screen can show", async () => {
    mockFetch({ 'POST /api/sync': { status: 400, body: { error: { type: 'invalid_request', message: 'no source with id "x"' } } } });
    const error: unknown = await request('POST', '/api/sync', {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, kind: 'invalid_request', message: 'no source with id "x"' });
  });

  it('sends the person back to the login when the session ends under them', async () => {
    const onEnd = vi.fn();
    setUnauthorizedHandler(onEnd);
    mockFetch({ 'GET /api/overview': { status: 401, body: { error: { type: 'unauthorized', message: 'log in' } } } });
    await request('GET', '/api/overview').catch(() => undefined);
    expect(onEnd).toHaveBeenCalledOnce();
  });

  it('leaves a failed login to the form instead of bouncing it', async () => {
    const onEnd = vi.fn();
    setUnauthorizedHandler(onEnd);
    mockFetch({ 'POST /api/session': { status: 401, body: { error: { type: 'unauthorized', message: 'nope' } } } });
    await request('POST', '/api/session', { token: 'x' }).catch(() => undefined);
    expect(onEnd).not.toHaveBeenCalled();
  });

  it('says so when the console cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed')));
    const error = (await request('GET', '/api/overview').catch((e: unknown) => e)) as Error;
    expect(error).toMatchObject({ status: 0, kind: 'network' });
    expect(error.message).toMatch(/kb-admin/);
  });
});
