import { Download } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api } from '../../api/api';
import type { RunSummary } from '../../api/types';
import { useConsole } from '../../components/ConsoleContext';
import { Badge, StatusBadge, TriggerBadge } from '../../components/ui/Badge';
import { Empty, Loading, LoadError, Page } from '../../components/ui/Page';
import { button, card, input } from '../../components/ui/styles';
import { downloadJson, formatDateTime, formatDuration } from '../../lib/format';
import { useResource } from '../../lib/useResource';
import { RunDetailModal } from './RunDetailModal';

const STATUSES = ['all', 'running', 'success', 'warning', 'error'] as const;
const MECHANISMS = [
  ['', 'Any kind'],
  ['incremental', 'Incremental'],
  ['full_scrape', 'Full'],
  ['lazy_refresh', 'Lazy refresh'],
  ['purge', 'Purge'],
] as const;
const PAGE = 50;

export function RunsView({ openRunId, onOpenRun, onCloseRun }: { openRunId: number | null; onOpenRun: (id: number) => void; onCloseRun: () => void }) {
  const { overview } = useConsole();
  const sources = useResource(api.sources, []);
  const [status, setStatus] = useState<(typeof STATUSES)[number]>('all');
  const [source, setSource] = useState('');
  const [mechanism, setMechanism] = useState('');
  const [more, setMore] = useState<RunSummary[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const filters = { source, status: status === 'all' ? '' : status, mode: mechanism };
  const first = useResource(
    () => api.runs({ ...filters, limit: PAGE }),
    [status, source, mechanism],
    (data) => (data?.runs.some((r) => r.status === 'running') ? 3000 : 15000),
  );

  useEffect(() => {
    setMore([]);
    setExhausted(false);
  }, [status, source, mechanism]);

  const head = first.data?.runs ?? [];
  const seen = new Set(head.map((r) => r.id));
  const runs = [...head, ...more.filter((r) => !seen.has(r.id))];
  const cursor = runs.length ? runs[runs.length - 1]!.id : null;
  const canLoadMore = !exhausted && (first.data?.next_before != null || more.length > 0) && runs.length >= PAGE;

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const page = await api.runs({ ...filters, before: cursor, limit: PAGE });
      setMore((current) => [...current, ...page.runs]);
      if (page.runs.length < PAGE) setExhausted(true);
    } finally {
      setLoadingMore(false);
    }
  };

  if (!first.data) return first.error ? <LoadError error={first.error} onRetry={first.reload} /> : <Loading />;

  return (
    <Page
      title="Reports"
      description="Every run, whoever started it: what it saw, what changed in the catalog, how long it took, and what it could not use."
      onRefresh={first.reload}
    >
      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="scrollbar-none flex items-center gap-1.5 overflow-x-auto">
          {STATUSES.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatus(s)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium capitalize ${
                status === s ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source" className={`${input} w-44`}>
            <option value="">Any source</option>
            {(sources.data?.sources ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.id}
              </option>
            ))}
          </select>
          <select value={mechanism} onChange={(e) => setMechanism(e.target.value)} aria-label="Kind of run" className={`${input} w-36`}>
            {MECHANISMS.map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </div>
      </div>

      {runs.length === 0 ? (
        <Empty>No run matches.</Empty>
      ) : (
        <div className={`${card} overflow-hidden`}>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-800 bg-slate-950/80 text-[10px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-semibold">Run</th>
                  <th className="px-4 py-3 font-semibold">Kind</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Catalog changes</th>
                  <th className="px-4 py-3 font-semibold">Text kept / purged</th>
                  <th className="px-4 py-3 font-semibold">Took</th>
                  <th className="px-4 py-3 font-semibold">Started</th>
                  <th className="px-4 py-3 text-right font-semibold">
                    <span className="sr-only">Export</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {runs.map((run) => (
                  <tr key={run.id} onClick={() => onOpenRun(run.id)} className="cursor-pointer hover:bg-slate-800/40">
                    <td className="px-4 py-3.5">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenRun(run.id);
                        }}
                        className="block text-left"
                      >
                        <span className="block font-semibold text-slate-100">{run.source_id}</span>
                        <span className="font-mono text-[11px] text-slate-500">#{run.id}</span>
                      </button>
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-slate-300">{run.mechanism.replace('_', ' ')}</span>
                        <TriggerBadge trigger={run.triggered_by} />
                        {run.dry_run ? <Badge tone="cyan">dry run</Badge> : null}
                        {run.forced ? <Badge tone="amber">forced</Badge> : null}
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <StatusBadge status={run.status} />
                      {run.rejected_count ? <span className="ml-2 text-[11px] text-amber-300">{run.rejected_count} rejected</span> : null}
                    </td>
                    <td className="px-4 py-3.5 font-mono text-[11px]">
                      <span className="text-emerald-400">+{run.created}</span> <span className="text-indigo-400">~{run.updated}</span>{' '}
                      <span className="text-rose-400">−{run.missing}</span>
                    </td>
                    <td className="px-4 py-3.5 font-mono text-[11px] text-slate-300">
                      <span className="text-purple-400">{run.content_stored}</span> / <span className="text-amber-400">{run.content_purged}</span>
                    </td>
                    <td className="px-4 py-3.5 text-slate-400">{formatDuration(run.duration_ms)}</td>
                    <td className="px-4 py-3.5 text-slate-400">{formatDateTime(run.started_at)}</td>
                    <td className="px-4 py-3.5 text-right">
                      <button
                        type="button"
                        className={button.icon}
                        title="Export this report as JSON"
                        aria-label={`Export run ${run.id}`}
                        onClick={async (e) => {
                          e.stopPropagation();
                          downloadJson(`kb-run-${run.id}.json`, await api.run(run.id));
                        }}
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {canLoadMore ? (
            <div className="border-t border-slate-800 p-3 text-center">
              <button type="button" className={button.secondary} onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? 'Loading…' : 'Load older runs'}
              </button>
            </div>
          ) : null}
        </div>
      )}

      {openRunId !== null ? <RunDetailModal runId={openRunId} now={overview.data?.now} onClose={onCloseRun} /> : null}
    </Page>
  );
}
