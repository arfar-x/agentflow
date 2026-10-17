import type { MechanismSettings, SourceCard, SourceKind } from '../api/types';

// The edit form's own shape, and the two translations between it and the
// settings the server stores. Kept out of the component so the rules -- what is
// sent, what counts as "cleared", what stays inherited -- can be tested.

export type Tri = 'inherit' | 'on' | 'off';
export const MECHANISMS = ['incremental', 'full_scrape', 'webhook', 'lazy_refresh'] as const;
export type MechanismName = (typeof MECHANISMS)[number];

export interface MechForm {
  enabled: Tri;
  every: string;
  at: string;
  stale_after: string;
}
export interface ScopeForm {
  dir: string;
  patterns: string;
  exclude: string;
  type: string;
  tags: string;
}
export interface ProjectForm {
  path: string;
  ref: string;
  scopes: ScopeForm[];
}
export interface SourceForm {
  id: string;
  kind: SourceKind;
  enabled: boolean;
  spaces: string;
  excludeLabels: string;
  labelTypes: { label: string; type: string }[];
  jql: string;
  baseUrl: string;
  tokenEnv: string;
  projects: ProjectForm[];
  storeRaw: boolean;
  maxBytes: string;
  mechanisms: Record<MechanismName, MechForm>;
}

export const SOURCE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

const blankMech = (): MechForm => ({ enabled: 'inherit', every: '', at: '', stale_after: '' });
const blankScope = (): ScopeForm => ({ dir: '.', patterns: '', exclude: '', type: '', tags: '' });
export const blankProject = (): ProjectForm => ({ path: '', ref: '', scopes: [] });
export { blankScope };

export function blankForm(kind: SourceKind): SourceForm {
  return {
    id: '',
    kind,
    enabled: false, // a new source starts off, so it can be dry-run before it syncs
    spaces: '',
    excludeLabels: '',
    labelTypes: [],
    jql: '',
    baseUrl: '',
    tokenEnv: 'GITLAB_TOKEN',
    projects: [],
    storeRaw: false, // never on by default: it shares the text with every catalog user
    maxBytes: '',
    mechanisms: {
      incremental: blankMech(),
      full_scrape: blankMech(),
      webhook: blankMech(),
      lazy_refresh: blankMech(),
    },
  };
}

export const csv = (text: string): string[] =>
  text
    .split(/[,\n]/)
    .map((part) => part.trim())
    .filter(Boolean);

const joined = (value: unknown): string => (Array.isArray(value) ? value.map(String).join(', ') : '');

export function formFromCard(card: SourceCard): SourceForm {
  const s = card.settings as Record<string, unknown>;
  const form = blankForm(card.kind as SourceKind);
  form.id = card.id;
  form.enabled = card.enabled;
  form.storeRaw = card.store_raw_content;
  // The cap shown is the source's own; blank means "the default applies".
  form.maxBytes = card.own_raw_content_max_bytes ? String(card.own_raw_content_max_bytes) : '';
  form.spaces = joined(s.spaces);
  form.excludeLabels = joined(s.exclude_labels);
  form.labelTypes = Object.entries((s.type_from_labels as Record<string, string> | undefined) ?? {}).map(([label, type]) => ({ label, type }));
  form.jql = String(s.jql ?? '');
  form.baseUrl = String(s.base_url ?? '');
  form.tokenEnv = String(s.token_env ?? 'GITLAB_TOKEN');
  form.projects = ((s.projects as { path: string; ref?: string | null; scopes?: Record<string, unknown>[] }[] | undefined) ?? []).map((p) => ({
    path: p.path,
    ref: p.ref ?? '',
    scopes: (p.scopes ?? []).map((sc) => ({
      dir: String(sc.dir ?? '.'),
      patterns: joined(sc.patterns),
      exclude: joined(sc.exclude),
      type: String(sc.type ?? ''),
      tags: joined(sc.tags),
    })),
  }));
  for (const name of MECHANISMS) {
    const own: MechanismSettings = card.own_mechanisms[name] ?? {};
    form.mechanisms[name] = {
      enabled: own.enabled === undefined ? 'inherit' : own.enabled ? 'on' : 'off',
      every: own.every ?? '',
      at: own.at ?? '',
      stale_after: own.stale_after ?? '',
    };
  }
  return form;
}

function mechanismsOf(form: SourceForm): Record<string, MechanismSettings> {
  const out: Record<string, MechanismSettings> = {};
  for (const name of MECHANISMS) {
    const m = form.mechanisms[name];
    const one: MechanismSettings = {};
    if (m.enabled !== 'inherit') one.enabled = m.enabled === 'on';
    if (m.every.trim()) one.every = m.every.trim();
    if (m.at.trim()) one.at = m.at.trim();
    if (m.stale_after.trim()) one.stale_after = m.stale_after.trim();
    if (Object.keys(one).length) out[name] = one;
  }
  return out;
}

/** The settings to send. A setting left empty is sent empty -- the server reads
 *  that as "clear it" -- so emptying a field removes it from the file. */
export function formToConfig(form: SourceForm, mode: 'create' | 'update'): Record<string, unknown> {
  const config: Record<string, unknown> = {
    enabled: form.enabled,
    store_raw_content: form.storeRaw,
    raw_content_max_bytes: form.maxBytes.trim() ? Number(form.maxBytes) : null,
    mechanisms: mechanismsOf(form),
  };
  if (mode === 'create') {
    config.id = form.id.trim();
    config.kind = form.kind;
  }
  switch (form.kind) {
    case 'confluence':
      config.spaces = csv(form.spaces);
      config.exclude_labels = csv(form.excludeLabels);
      config.type_from_labels = Object.fromEntries(
        form.labelTypes.filter((p) => p.label.trim() && p.type.trim()).map((p) => [p.label.trim(), p.type.trim()]),
      );
      break;
    case 'jira':
      config.jql = form.jql.trim();
      break;
    case 'gitlab':
      config.base_url = form.baseUrl.trim();
      config.token_env = form.tokenEnv.trim();
      config.projects = form.projects.map((p) => ({
        path: p.path.trim(),
        ...(p.ref.trim() ? { ref: p.ref.trim() } : {}),
        ...(p.scopes.length
          ? {
              scopes: p.scopes.map((sc) => ({
                dir: sc.dir.trim() || '.',
                ...(csv(sc.patterns).length ? { patterns: csv(sc.patterns) } : {}),
                ...(csv(sc.exclude).length ? { exclude: csv(sc.exclude) } : {}),
                ...(sc.type.trim() ? { type: sc.type.trim() } : {}),
                ...(csv(sc.tags).length ? { tags: csv(sc.tags) } : {}),
              })),
            }
          : {}),
      }));
      break;
  }
  // On create, nothing the form left empty is worth writing.
  if (mode === 'create') {
    for (const key of Object.keys(config)) {
      const v = config[key];
      if (v === null || (Array.isArray(v) && !v.length) || (typeof v === 'object' && v !== null && !Array.isArray(v) && !Object.keys(v).length)) delete config[key];
    }
  }
  return config;
}

/** What is wrong with the form, found before a round trip. The server stays the
 *  authority; this only saves an obvious mistake a trip. */
export function problems(form: SourceForm, mode: 'create' | 'update'): string[] {
  const found: string[] = [];
  if (mode === 'create') {
    if (!form.id.trim()) found.push('Give the source an id.');
    else if (!SOURCE_ID.test(form.id.trim())) found.push("The id may use letters, digits, '.', '_' and '-' (it becomes part of every entry's id).");
    else if (form.id.trim() === 'form') found.push("'form' is reserved for the manual records.");
  }
  if (form.kind === 'confluence' && !csv(form.spaces).length) found.push('List at least one space.');
  if (form.kind === 'jira' && !form.jql.trim()) found.push('Write the JQL that selects the issues to catalogue.');
  if (form.kind === 'gitlab') {
    if (!form.baseUrl.trim()) found.push('Give the GitLab base URL.');
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(form.tokenEnv.trim())) found.push('The token setting is the NAME of an environment variable (like GITLAB_TOKEN), never the token itself.');
    if (form.projects.some((p) => !p.path.trim())) found.push('Every project needs its path.');
  }
  if (form.maxBytes.trim() && !(Number(form.maxBytes) >= 1024)) found.push('The text cap must be a number of at least 1024 bytes.');
  return found;
}
