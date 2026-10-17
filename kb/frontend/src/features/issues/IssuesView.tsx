import { AlertTriangle, KeyRound, Search, ServerCrash } from 'lucide-react';

import { api } from '../../api/api';
import { useConsole } from '../../components/ConsoleContext';
import { Badge, StatusBadge } from '../../components/ui/Badge';
import { Loading, LoadError, Notice, Page } from '../../components/ui/Page';
import { card, sectionTitle } from '../../components/ui/styles';
import { formatDateTime, relativeTime } from '../../lib/format';
import { useResource } from '../../lib/useResource';

export function IssuesView({ onOpenRun, onWriteRecord }: { onOpenRun: (id: number) => void; onWriteRecord?: (query: string) => void }) {
  const { overview } = useConsole();
  const issues = useResource(api.issues, [], 20000);
  if (!issues.data) return issues.error ? <LoadError error={issues.error} onRetry={issues.reload} /> : <Loading />;
  const data = issues.data;
  const now = overview.data?.now ?? new Date().toISOString();
  const quiet = !data.config_error && !data.runs.length && !data.missing_credentials.length && !data.refresh_queue.failures.length && data.summarizer?.state !== 'failing';

  return (
    <Page
      title="Issues"
      description="What needs attention, and what people searched for and did not find."
      onRefresh={issues.reload}
    >
      {data.config_error ? <Notice tone="rose">The source configuration cannot be read: {data.config_error}</Notice> : null}
      {data.summarizer?.state === 'failing' ? (
        <Notice tone="rose">
          <strong>The summarizer is failing its check</strong>, so scheduled runs refuse to catalogue documents undescribed: {data.summarizer.error ?? 'no detail'}. Run <code>make kb-check</code> for the full result.
        </Notice>
      ) : null}
      {quiet ? <Notice tone="indigo">No failed runs, rejected documents or missing credentials. </Notice> : null}

      {data.missing_credentials.length ? (
        <div className={`${card} p-5`}>
          <h2 className={`${sectionTitle} mb-3 flex items-center gap-2`}>
            <KeyRound className="h-4 w-4 text-amber-400" /> Enabled sources the scheduler cannot authenticate
          </h2>
          <ul className="space-y-2 text-xs">
            {data.missing_credentials.map((m) => (
              <li key={m.source_id} className="rounded-lg bg-slate-950/60 p-3">
                <span className="font-semibold text-slate-100">{m.source_id}</span> <Badge tone="slate">{m.kind}</Badge>
                <p className="mt-1 font-mono text-[11px] text-slate-400">
                  {Object.entries(m.variables).map(([name, set]) => (
                    <span key={name} className={`mr-3 ${set ? 'text-emerald-400' : 'text-slate-500'}`}>
                      {set ? '✓' : '✗'} {name}
                    </span>
                  ))}
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-slate-500">Set the variables in <code>.env</code> and restart <code>kb-scheduler</code>.</p>
        </div>
      ) : null}

      {data.runs.length ? (
        <div className={`${card} p-5`}>
          <h2 className={`${sectionTitle} mb-3 flex items-center gap-2`}>
            <ServerCrash className="h-4 w-4 text-rose-400" /> Runs that failed or rejected documents
          </h2>
          <ul className="divide-y divide-slate-800/80">
            {data.runs.map((run) => (
              <li key={run.id} className="py-3">
                <button type="button" className="w-full text-left" onClick={() => onOpenRun(run.id)}>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <span className="font-mono text-slate-500">#{run.id}</span>
                    <span className="font-semibold text-slate-100">{run.source_id}</span>
                    <StatusBadge status={run.status} />
                    <span className="text-slate-500">{relativeTime(run.started_at, now)}</span>
                  </div>
                  {run.error ? <p className="mt-1.5 text-xs text-rose-300">{run.error}</p> : null}
                  {run.rejected.length ? (
                    <p className="mt-1.5 font-mono text-[11px] text-amber-200">
                      {run.rejected.slice(0, 3).join(' · ')}
                      {run.rejected.length > 3 ? ` · and ${run.rejected.length - 3} more` : ''}
                    </p>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.refresh_queue.failures.length ? (
        <div className={`${card} p-5`}>
          <h2 className={`${sectionTitle} mb-3 flex items-center gap-2`}>
            <AlertTriangle className="h-4 w-4 text-amber-400" /> Refreshes that keep failing
          </h2>
          <ul className="space-y-2 text-xs">
            {data.refresh_queue.failures.map((f) => (
              <li key={f.entry_id} className="rounded-lg bg-slate-950/60 p-3">
                <span className="font-mono text-slate-200">{f.entry_id}</span>
                <span className="ml-2 text-slate-500">
                  {f.attempts} attempts · asked {formatDateTime(f.requested_at)}
                </span>
                <p className="mt-1 text-rose-300">{f.last_error}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className={`${card} p-5`}>
        <h2 className={`${sectionTitle} mb-1 flex items-center gap-2`}>
          <Search className="h-4 w-4 text-cyan-400" /> Searches that found nothing
        </h2>
        <p className="mb-3 text-xs text-slate-400">
          Evidence of knowledge nobody has written down. The most asked come first.
        </p>
        {data.gaps.length ? (
          <ul className="divide-y divide-slate-800/80">
            {data.gaps.map((gap) => (
              <li key={gap.query} className="flex items-center justify-between gap-3 py-2.5 text-xs">
                <span className="text-slate-200">{gap.query}</span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="text-slate-500">last {relativeTime(gap.last_seen, now)}</span>
                  <Badge tone="cyan">{gap.times}×</Badge>
                  {onWriteRecord ? (
                    <button type="button" onClick={() => onWriteRecord(gap.query)} className="font-medium text-indigo-400 hover:text-indigo-300">
                      Write a record →
                    </button>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-4 text-center text-xs text-slate-500">Every search so far found something.</p>
        )}
      </div>
    </Page>
  );
}
