import { ArrowUpRight, HardDrive, Layers, Stethoscope, TrendingUp, FileCode, Clock } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { api } from '../../api/api';
import type { SourceCard } from '../../api/types';
import { useConsole } from '../../components/ConsoleContext';
import { Badge, StatusBadge, StatusDot, healthStyle, runTone } from '../../components/ui/Badge';
import { Loading, LoadError, Notice, Page } from '../../components/ui/Page';
import { button, card, sectionTitle } from '../../components/ui/styles';
import { formatBytes, formatDateTime, formatDuration, percent, relativeTime } from '../../lib/format';
import { useResource } from '../../lib/useResource';
import { CheckModal } from './CheckModal';
import { QueuePanel } from '../sync/QueuePanel';

function Kpi({ label, icon, tone, children, foot }: { label: string; icon: ReactNode; tone: string; children: ReactNode; foot: ReactNode }) {
  return (
    <div className={`${card} p-5 transition hover:border-slate-700`}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-400">{label}</span>
        <span className={`rounded-lg p-2 ${tone}`}>{icon}</span>
      </div>
      <div className="mt-3 flex items-baseline gap-2">{children}</div>
      <div className="mt-2 text-[11px] text-slate-400">{foot}</div>
    </div>
  );
}

export function OverviewView({ onNavigate }: { onNavigate: (name: string, param?: string | number) => void }) {
  const { overview, openTrigger } = useConsole();
  const sources = useResource(api.sources, [], 15000);
  const recent = useResource(() => api.runs({ limit: 6 }), [], 15000);
  const [checking, setChecking] = useState(false);

  const data = overview.data;
  if (!data) return overview.error ? <LoadError error={overview.error} onRetry={overview.reload} /> : <Loading />;

  const style = healthStyle(data.health);
  const rate = data.runs_24h.success_rate;

  return (
    <Page
      title="Overview"
      description="Is the catalog working, what changed lately, and what needs doing."
      onRefresh={() => {
        overview.reload();
        sources.reload();
        recent.reload();
      }}
      actions={
        <>
          <button type="button" className={button.secondary} onClick={() => setChecking(true)}>
            <Stethoscope className="h-3.5 w-3.5" /> Run check
          </button>
        </>
      }
    >
      <div className={`${card} p-5`}>
        <div className="flex flex-wrap items-center gap-3">
          <span className={`h-3 w-3 rounded-full ${style.dot}`} aria-hidden="true" />
          <h2 className={`text-lg font-bold ${style.text}`}>{style.label}</h2>
          <span className="text-xs text-slate-500">
            {data.scheduler
              ? data.scheduler.alive
                ? `Scheduler reported ${relativeTime(data.scheduler.beat_at, data.now)}`
                : `Scheduler silent since ${formatDateTime(data.scheduler.beat_at)}`
              : 'Scheduler has not reported'}
            {data.scheduler?.working_on ? ` · running ${data.scheduler.working_on}` : ''}
          </span>
        </div>
        {data.problems.length ? (
          <ul className="mt-3 space-y-1 text-xs text-slate-300">
            {data.problems.map((problem) => (
              <li key={problem} className="flex gap-2">
                <span className="text-amber-400">•</span>
                <span>{problem}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-xs text-slate-400">Nothing needs attention.</p>
        )}
      </div>

      {!data.approved.value ? (
        <Notice tone="amber">
          <strong>Syncing is switched off</strong> (
          {data.approved.from === 'environment' ? 'KB_SOURCES_APPROVED=false in the environment' : '`approved: false` in the source configuration'}
          ). Nothing is scheduled and requested syncs are refused until it is switched back on.
        </Notice>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          label="Sources"
          icon={<Layers className="h-4 w-4" />}
          tone="bg-indigo-500/10 text-indigo-400"
          foot={<span className="font-semibold text-emerald-400">{data.sources.enabled} enabled</span>}
        >
          <span className="text-2xl font-bold text-white">{data.sources.configured}</span>
          <span className="text-xs text-slate-400">configured</span>
        </Kpi>
        <Kpi
          label="Catalog entries"
          icon={<FileCode className="h-4 w-4" />}
          tone="bg-cyan-500/10 text-cyan-400"
          foot={
            <>
              {data.entries.soft_deleted} removed at the source · {data.overrides} overrides
            </>
          }
        >
          <span className="text-2xl font-bold text-white">{data.entries.live}</span>
          <span className="text-xs text-slate-400">live</span>
        </Kpi>
        <Kpi
          label="Stored text"
          icon={<HardDrive className="h-4 w-4" />}
          tone="bg-purple-500/10 text-purple-400"
          foot={
            <>
              {data.raw_content.documents} documents
              {data.raw_content.truncated ? ` · ${data.raw_content.truncated} truncated` : ''}
            </>
          }
        >
          <span className="text-2xl font-bold text-white">{formatBytes(data.raw_content.text_bytes)}</span>
          <span className="text-xs text-slate-400">readable by every KB user</span>
        </Kpi>
        <Kpi
          label="Last 24 hours"
          icon={<TrendingUp className="h-4 w-4" />}
          tone="bg-emerald-500/10 text-emerald-400"
          foot={
            <>
              {data.runs_24h.runs} runs · {data.runs_24h.failed} failed · {data.runs_24h.warned} with warnings
            </>
          }
        >
          <span className={`text-2xl font-bold ${data.runs_24h.failed ? 'text-amber-400' : 'text-emerald-400'}`}>{percent(rate)}</span>
          <span className="text-xs text-slate-400">succeeded</span>
        </Kpi>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className={`${card} p-5 lg:col-span-2`}>
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className={sectionTitle}>Sources</h2>
              <p className="text-xs text-slate-400">Where each one stands, and what its text policy is.</p>
            </div>
            <button type="button" onClick={() => onNavigate('sources')} className="flex items-center gap-1 text-xs font-medium text-indigo-400 hover:text-indigo-300">
              View all <ArrowUpRight className="h-3.5 w-3.5" />
            </button>
          </div>
          {sources.data ? (
            sources.data.sources.length ? (
              <div className="divide-y divide-slate-800/80">
                {sources.data.sources.map((source) => (
                  <SourceLine key={source.id} source={source} now={data.now} onSync={() => openTrigger(source.id)} onOpen={(id) => onNavigate('reports', id)} />
                ))}
              </div>
            ) : (
              <p className="py-6 text-center text-xs text-slate-500">
                {sources.data.config_error ? `The source configuration cannot be read: ${sources.data.config_error}` : 'No sources are configured yet.'}
              </p>
            )
          ) : (
            <Loading />
          )}
        </div>

        <div className="space-y-6">
          <QueuePanel />
          <div className={`${card} p-5`}>
            <h2 className={`${sectionTitle} mb-3`}>Catalog health</h2>
            <dl className="space-y-2 text-xs">
              <Row k="Searches with no answer" v={String(data.gaps)} onClick={() => onNavigate('issues')} />
              <Row k="Reads waiting to refresh" v={String(data.refresh_queue.pending)} />
              <Row k="Refreshes given up on" v={String(data.refresh_queue.gave_up)} warn={data.refresh_queue.gave_up > 0} />
              <Row
                k="Summarizer"
                v={
                  data.scheduler?.summarizer === 'ok'
                    ? 'working'
                    : data.scheduler?.summarizer === 'failing'
                      ? 'failing'
                      : data.scheduler?.summarizer === 'none'
                        ? 'not configured'
                        : 'not checked yet'
                }
                warn={data.scheduler?.summarizer === 'failing'}
              />
            </dl>
          </div>
        </div>
      </div>

      <div className={`${card} p-5`}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className={sectionTitle}>Recent runs</h2>
          <button type="button" onClick={() => onNavigate('reports')} className="flex items-center gap-1 text-xs font-medium text-indigo-400 hover:text-indigo-300">
            All reports <ArrowUpRight className="h-3.5 w-3.5" />
          </button>
        </div>
        {recent.data?.runs.length ? (
          <ul className="divide-y divide-slate-800/80">
            {recent.data.runs.map((run) => (
              <li key={run.id}>
                <button
                  type="button"
                  onClick={() => onNavigate('reports', run.id)}
                  className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 py-2.5 text-left text-xs hover:bg-slate-800/40"
                >
                  <span className="w-12 font-mono text-slate-500">#{run.id}</span>
                  <span className="min-w-32 font-semibold text-slate-200">{run.source_id}</span>
                  <span className="text-slate-400">{run.mechanism.replace('_', ' ')}{run.dry_run ? ' · dry run' : ''}</span>
                  <StatusBadge status={run.status} />
                  <span className="font-mono text-slate-300">
                    <span className="text-emerald-400">+{run.created}</span> <span className="text-indigo-400">~{run.updated}</span>{' '}
                    <span className="text-rose-400">−{run.missing}</span>
                  </span>
                  <span className="ml-auto text-slate-500">{run.finished_at ? `${relativeTime(run.finished_at, data.now)} · ${formatDuration(run.duration_ms)}` : 'running…'}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-6 text-center text-xs text-slate-500">Nothing has run yet.</p>
        )}
      </div>

      {checking ? <CheckModal onClose={() => setChecking(false)} /> : null}
    </Page>
  );
}

function Row({ k, v, warn, onClick }: { k: string; v: string; warn?: boolean; onClick?: () => void }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-slate-400">{k}</dt>
      <dd>
        {onClick ? (
          <button type="button" onClick={onClick} className="font-semibold text-indigo-400 hover:text-indigo-300">
            {v}
          </button>
        ) : (
          <span className={`font-semibold ${warn ? 'text-amber-400' : 'text-slate-200'}`}>{v}</span>
        )}
      </dd>
    </div>
  );
}

function SourceLine({ source, now, onSync, onOpen }: { source: SourceCard; now: string; onSync: () => void; onOpen: (runId: number) => void }) {
  const last = source.last_run;
  return (
    <div className="flex flex-col justify-between gap-3 py-3.5 sm:flex-row sm:items-center">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-slate-100">{source.id}</span>
          <Badge tone="slate">{source.kind}</Badge>
          {!source.enabled ? <Badge tone="amber">disabled</Badge> : null}
          {source.store_raw_content ? <Badge tone="purple">stores text</Badge> : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
          <span>{source.entries} entries</span>
          {source.content.documents ? <span>{formatBytes(source.content.text_bytes)} of text</span> : null}
          {last ? (
            <button type="button" onClick={() => onOpen(last.id)} className="inline-flex items-center gap-1.5 hover:text-slate-200">
              <StatusDot tone={runTone(last.status)} />
              {last.status} · {last.finished_at ? relativeTime(last.finished_at, now) : 'in progress'}
            </button>
          ) : (
            <span className="inline-flex items-center gap-1.5">
              <Clock className="h-3 w-3" /> never synced
            </span>
          )}
        </div>
      </div>
      <button type="button" className={button.secondary} onClick={onSync}>
        Sync…
      </button>
    </div>
  );
}
