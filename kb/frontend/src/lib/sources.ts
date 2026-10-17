import type { MechanismSettings, SourceCard } from '../api/types';

/** The few settings worth showing on a card, per kind; the full set is the YAML. */
export function describeSettings(source: SourceCard): [string, string][] {
  const s = source.settings as Record<string, unknown>;
  const list = (value: unknown) => (Array.isArray(value) ? value.map(String).join(', ') : '');
  const rows: [string, string][] = [];
  switch (source.kind) {
    case 'confluence':
      rows.push(['Spaces', list(s.spaces) || '—']);
      if (s.exclude_labels && (s.exclude_labels as unknown[]).length) rows.push(['Excluded labels', list(s.exclude_labels)]);
      if (s.type_from_labels && Object.keys(s.type_from_labels as object).length)
        rows.push(['Label → type', Object.entries(s.type_from_labels as Record<string, string>).map(([k, v]) => `${k} → ${v}`).join(', ')]);
      break;
    case 'jira':
      rows.push(['JQL', String(s.jql ?? '—')]);
      break;
    case 'gitlab': {
      rows.push(['Base URL', String(s.base_url ?? '—')]);
      const projects = (s.projects as { path: string }[] | undefined) ?? [];
      rows.push(['Projects', projects.map((p) => p.path).join(', ') || '—']);
      break;
    }
    default:
      Object.entries(s).forEach(([k, v]) => rows.push([k, typeof v === 'string' ? v : JSON.stringify(v)]));
  }
  return rows;
}

export function describeMechanisms(mechanisms: Record<string, MechanismSettings>): { name: string; on: boolean; detail: string }[] {
  const m = mechanisms;
  return [
    { name: 'Incremental', on: !!m.incremental?.enabled, detail: m.incremental?.every ? `every ${m.incremental.every}` : '' },
    { name: 'Nightly full', on: !!m.full_scrape?.enabled, detail: m.full_scrape?.at ? `at ${m.full_scrape.at}` : '' },
    { name: 'Webhook', on: !!m.webhook?.enabled, detail: '' },
    { name: 'Lazy refresh', on: !!m.lazy_refresh?.enabled, detail: m.lazy_refresh?.stale_after ? `stale after ${m.lazy_refresh.stale_after}` : '' },
  ];
}

/** Whether the scheduler's environment has what this kind of source needs.
 *  Mirrors source_factory.build_source, which is the authority; this only
 *  decides whether to warn before a run fails for want of them. */
export function credentialsMissing(source: SourceCard): string[] {
  const vars = source.credentials;
  if (!Object.keys(vars).length) return [];
  const has = (...names: string[]) => names.some((n) => vars[n]);
  if (source.kind === 'confluence' || source.kind === 'jira') {
    const p = source.kind.toUpperCase();
    const base = has(`KB_${p}_BASE_URL`, `${p}_BASE_URL`);
    const auth = has(`KB_${p}_PAT`, `${p}_PAT`) || (has(`KB_${p}_USERNAME`, `${p}_USERNAME`) && has(`KB_${p}_PASSWORD`, `${p}_PASSWORD`));
    const missing: string[] = [];
    if (!base) missing.push(`KB_${p}_BASE_URL`);
    if (!auth) missing.push(`KB_${p}_PAT (or KB_${p}_USERNAME + KB_${p}_PASSWORD)`);
    return missing;
  }
  return Object.entries(vars).filter(([, set]) => !set).map(([name]) => name);
}
