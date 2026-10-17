// What the console's API returns (kb/src/kb/adapters/inbound/admin). Kept by
// hand next to the backend's views.py: a field added there is added here.

export type Health = 'healthy' | 'syncing' | 'degraded' | 'error';
export type RunStatus = 'running' | 'success' | 'warning' | 'error';
export type Trigger = 'scheduled' | 'manual' | 'webhook' | 'cli';
export type RequestStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
export type SyncMode = 'incremental' | 'full';
export type SourceKind = 'confluence' | 'jira' | 'gitlab';

export interface RunSummary {
  id: number;
  source_id: string;
  mechanism: string;
  mode: string | null;
  status: RunStatus;
  triggered_by: Trigger;
  dry_run: boolean;
  forced: boolean;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  seen: number;
  created: number;
  updated: number;
  unchanged: number;
  revived: number;
  missing: number;
  summarized: number;
  content_stored: number;
  content_purged: number;
  rejected_count: number;
  error: string | null;
}

export interface RunDetail extends RunSummary {
  rejected: string[];
  checkpoint: string | null;
}

export interface SchedulerView {
  alive: boolean;
  beat_at: string;
  working_on: string | null;
  summarizer: 'none' | 'unknown' | 'ok' | 'failing';
  summarizer_error: string | null;
}

export interface Approval {
  value: boolean;
  from: string;
}

export interface Overview {
  now: string;
  health: Health;
  problems: string[];
  scheduler: SchedulerView | null;
  approved: Approval;
  config_error: string | null;
  entries: { live: number; soft_deleted: number; searchable: number };
  by_type: { type: string; entries: number }[];
  overrides: number;
  gaps: number;
  raw_content: { documents: number; truncated: number; text_bytes: number; disk_bytes: number };
  runs_24h: {
    runs: number;
    succeeded: number;
    warned: number;
    failed: number;
    running: number;
    success_rate: number | null;
  };
  queue: { queued: number; running: number };
  refresh_queue: RefreshQueue;
  sources: { configured: number; enabled: number };
  last_run: RunSummary | null;
}

export interface RefreshQueue {
  pending: number;
  gave_up: number;
  failures: { entry_id: string; attempts: number; last_error: string; requested_at: string }[];
}

export interface MechanismSettings {
  enabled?: boolean;
  every?: string;
  at?: string;
  stale_after?: string;
}

export interface SourceCard {
  id: string;
  kind: SourceKind | string;
  enabled: boolean;
  mechanisms: Record<string, MechanismSettings>;
  store_raw_content: boolean;
  raw_content_max_bytes: number;
  settings: Record<string, unknown>;
  entries: number;
  content: { documents: number; text_bytes: number };
  last_run: RunSummary | null;
  credentials: Record<string, boolean>;
  origin: 'yaml' | 'ui' | 'builtin';
}

export interface SourcesResponse {
  config_error: string | null;
  approved?: Approval;
  sources: SourceCard[];
}

export interface ScheduleRow {
  kind: 'incremental' | 'full_scrape' | 'lazy_refresh';
  source_id: string | null;
  enabled: boolean;
  reason: string | null;
  cadence: string;
  last_run: string | null;
  due_at: string | null;
  running: boolean;
  last_result: RunSummary | null;
}

export interface SchedulesResponse {
  now: string;
  approved?: Approval;
  config_error: string | null;
  rows: ScheduleRow[];
}

export interface RunsResponse {
  runs: RunSummary[];
  next_before: number | null;
}

export interface SyncRequestView {
  id: number;
  kind: 'sync' | 'purge_content';
  source_id: string;
  mode: string | null;
  dry_run: boolean;
  forced: boolean;
  status: RequestStatus;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  run_ids: number[];
  error: string | null;
  position: number | null;
}

export interface Gap {
  query: string;
  times: number;
  last_seen: string;
}

export interface IssuesResponse {
  config_error: string | null;
  summarizer: { state: string; error: string | null } | null;
  runs: RunDetail[];
  missing_credentials: { source_id: string; kind: string; variables: Record<string, boolean> }[];
  refresh_queue: RefreshQueue;
  gaps: Gap[];
}

export interface ConfigResponse {
  languages: string[];
  summarizer_configured: boolean;
  entry_types: string[];
  sources_file: string;
  config_error: string | null;
  kinds: SourceKind[];
}

export interface CheckResponse {
  ready: boolean;
  checks: Record<string, { ok: boolean; error?: string | null } & Record<string, unknown>>;
}
