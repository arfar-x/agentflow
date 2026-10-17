import { Calendar, Play } from 'lucide-react';

import { api } from '../../api/api';
import type { ScheduleRow } from '../../api/types';
import { useConsole } from '../../components/ConsoleContext';
import { Badge, StatusBadge } from '../../components/ui/Badge';
import { Empty, Loading, LoadError, Notice, Page } from '../../components/ui/Page';
import { button, card } from '../../components/ui/styles';
import { humanize, relativeTime } from '../../lib/format';
import { useResource } from '../../lib/useResource';
import { QueuePanel } from '../sync/QueuePanel';

function dueText(row: ScheduleRow, now: string): string {
  if (row.running) return 'running now';
  if (!row.enabled) return '—';
  if (row.kind === 'lazy_refresh') return row.source_id ? 'when a stale entry is read' : row.due_at ? relativeTime(row.due_at, now) : '—';
  if (!row.due_at) return 'after its first run';
  const delta = new Date(row.due_at).getTime() - new Date(now).getTime();
  return delta <= 0 ? 'on the next tick' : relativeTime(row.due_at, now);
}

export function SchedulesView({ onNavigate }: { onNavigate: (name: string, param?: string | number | null) => void }) {
  const { openTrigger } = useConsole();
  const schedules = useResource(api.schedules, [], 10000);

  if (!schedules.data) return schedules.error ? <LoadError error={schedules.error} onRetry={schedules.reload} /> : <Loading />;
  const data = schedules.data;
  const bySource = new Map<string, ScheduleRow[]>();
  for (const row of data.rows) {
    const key = row.source_id ?? '(all sources)';
    bySource.set(key, [...(bySource.get(key) ?? []), row]);
  }

  return (
    <Page
      title="Schedules"
      description={
        <>
          When each source&apos;s mechanisms run, from <code className="text-indigo-300">mechanisms:</code> in the configuration, and how the last one went. Cadences are changed in the configuration; the next run time is computed the same way the scheduler computes it.
        </>
      }
      onRefresh={schedules.reload}
      actions={
        <button type="button" className={button.primary} onClick={() => openTrigger()}>
          <Play className="h-3.5 w-3.5" /> Run now…
        </button>
      }
    >
      {data.config_error ? <Notice tone="rose">The source configuration cannot be read, so no schedule can be shown: {data.config_error}</Notice> : null}
      {data.approved && !data.approved.value ? (
        <Notice tone="amber">
          <strong>Syncing is switched off</strong> ({data.approved.from === 'environment' ? 'KB_SOURCES_APPROVED=false' : '`approved: false` in the file'}), so nothing below will run.
        </Notice>
      ) : null}

      {data.rows.length === 0 && !data.config_error ? (
        <Empty>No sources are configured, so there is nothing to schedule.</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            {[...bySource.entries()].map(([source, rows]) => (
              <div key={source} className={`${card} overflow-hidden`}>
                <div className="flex items-center gap-2 border-b border-slate-800 bg-slate-950/60 px-4 py-2.5">
                  <Calendar className="h-4 w-4 text-slate-500" />
                  <h2 className="text-sm font-semibold text-slate-100">{source}</h2>
                  {rows[0]?.source_id ? (
                    <button type="button" className="ml-auto text-[11px] font-medium text-indigo-400 hover:text-indigo-300" onClick={() => onNavigate('sources', rows[0]!.source_id)}>
                      Change cadence →
                    </button>
                  ) : null}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-4 py-2 font-semibold">Mechanism</th>
                        <th className="px-4 py-2 font-semibold">Cadence</th>
                        <th className="px-4 py-2 font-semibold">Last result</th>
                        <th className="px-4 py-2 font-semibold">Next</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {rows.map((row) => (
                        <tr key={`${row.kind}-${row.source_id}`} className={row.enabled ? '' : 'opacity-60'}>
                          <td className="px-4 py-3">
                            <span className="font-medium text-slate-200">{humanize(row.kind)}</span>
                            {!row.enabled ? (
                              <span className="ml-2">
                                <Badge tone="slate" title={row.reason ?? undefined}>
                                  {row.reason ?? 'off'}
                                </Badge>
                              </span>
                            ) : null}
                          </td>
                          <td className="px-4 py-3 font-mono text-slate-300">{row.cadence}</td>
                          <td className="px-4 py-3">
                            {row.last_result ? (
                              <button type="button" onClick={() => onNavigate('reports', row.last_result!.id)} className="inline-flex items-center gap-2 hover:text-white">
                                <StatusBadge status={row.last_result.status} />
                                <span className="text-slate-400">{row.last_result.finished_at ? relativeTime(row.last_result.finished_at, data.now) : 'in progress'}</span>
                              </button>
                            ) : (
                              <span className="text-slate-600">no run yet</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-indigo-300">{dueText(row, data.now)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
          <div className="space-y-6">
            <QueuePanel />
            <Notice tone="indigo">
              A run someone asked for waits behind the one in progress: the scheduler does one job at a time, and never two for the same source.
            </Notice>
          </div>
        </div>
      )}
    </Page>
  );
}
