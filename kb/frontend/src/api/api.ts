import { del, get, post } from './client';
import type {
  ChangePreview,
  CheckResponse,
  ConfigResponse,
  IssuesResponse,
  Overview,
  RequestStatus,
  Revision,
  RunDetail,
  RunsResponse,
  SchedulesResponse,
  SourceChange,
  SourcesResponse,
  SyncMode,
  SyncRequestView,
  Gap,
} from './types';

export interface RunFilters {
  source?: string;
  status?: string;
  mode?: string;
  before?: number | null;
  limit?: number;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const api = {
  session: () => get<{ authenticated: boolean }>('/api/session'),
  login: (token: string) => post<{ authenticated: boolean }>('/api/session', { token }),
  logout: () => del<{ authenticated: boolean }>('/api/session'),

  config: () => get<ConfigResponse>('/api/config'),
  overview: () => get<Overview>('/api/overview'),
  sources: () => get<SourcesResponse>('/api/sources'),
  schedules: () => get<SchedulesResponse>('/api/schedules'),
  runs: (filters: RunFilters = {}) =>
    get<RunsResponse>(
      `/api/runs${query({
        source: filters.source,
        status: filters.status,
        mode: filters.mode,
        before: filters.before ?? undefined,
        limit: filters.limit,
      })}`,
    ),
  run: (id: number) => get<RunDetail>(`/api/runs/${id}`),
  issues: () => get<IssuesResponse>('/api/issues'),
  gaps: (limit = 50) => get<{ gaps: Gap[] }>(`/api/gaps${query({ limit })}`),
  check: () => post<CheckResponse>('/api/check'),

  requestSync: (body: { source_id: string; mode: SyncMode; dry_run: boolean; force: boolean }) =>
    post<{ request_id: number }>('/api/sync', body),
  previewSource: (change: SourceChange) => post<ChangePreview>('/api/sources/preview', { change }),
  applySource: (change: SourceChange, etag: string) =>
    post<{ changed: boolean; etag: string; revision_id: number | null }>('/api/sources/apply', {
      change,
      etag,
      confirm: true,
    }),
  revisions: (limit = 50) => get<{ revisions: Revision[] }>(`/api/sources/revisions${query({ limit })}`),
  retireSource: (sourceId: string) =>
    post<{ request_id: number }>(`/api/sources/${encodeURIComponent(sourceId)}/retire`),
  purgeContent: (sourceId: string) =>
    post<{ request_id: number }>(`/api/sources/${encodeURIComponent(sourceId)}/purge-content`),
  syncRequests: (statuses?: RequestStatus[]) =>
    get<{ requests: SyncRequestView[] }>(
      `/api/sync/requests${query({ status: statuses?.join(',') })}`,
    ),
  cancelRequest: (id: number) => del<{ cancelled: number }>(`/api/sync/requests/${id}`),
};
