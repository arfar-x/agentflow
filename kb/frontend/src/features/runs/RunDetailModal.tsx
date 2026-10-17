import { Download, FileCheck } from 'lucide-react';

import { api } from '../../api/api';
import { Badge, StatusBadge, TriggerBadge } from '../../components/ui/Badge';
import { ErrorNotice } from '../../components/ui/ConfirmDialog';
import { Modal } from '../../components/ui/Modal';
import { button } from '../../components/ui/styles';
import { downloadJson, formatDateTime, formatDuration, relativeTime } from '../../lib/format';
import { useResource } from '../../lib/useResource';

export function RunDetailModal({ runId, now, onClose }: { runId: number; now?: string; onClose: () => void }) {
  const run = useResource(() => api.run(runId), [runId], (data) => (data?.status === 'running' ? 3000 : null));
  const data = run.data;

  return (
    <Modal
      title={`Run #${runId}`}
      subtitle={data ? `${data.source_id} · ${data.mechanism.replace('_', ' ')}` : undefined}
      icon={<FileCheck className="h-5 w-5" />}
      width="max-w-2xl"
      onClose={onClose}
      footer={
        <>
          {data ? (
            <button type="button" className={button.secondary} onClick={() => downloadJson(`kb-run-${data.id}.json`, data)}>
              <Download className="h-3.5 w-3.5" /> Export JSON
            </button>
          ) : null}
          <button type="button" className={button.primary} onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      {run.error && !data ? <ErrorNotice message={run.error.message} /> : null}
      {!data && !run.error ? <p className="py-8 text-center text-xs text-slate-500">Loading…</p> : null}
      {data ? (
        <div className="space-y-4 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={data.status} />
            <TriggerBadge trigger={data.triggered_by} />
            {data.mode ? <Badge tone="slate">{data.mode}</Badge> : null}
            {data.dry_run ? <Badge tone="cyan">dry run — nothing written</Badge> : null}
            {data.forced ? <Badge tone="amber">forced re-summarization</Badge> : null}
          </div>

          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ['Started', formatDateTime(data.started_at)],
              ['Finished', data.finished_at ? formatDateTime(data.finished_at) : 'still running'],
              ['Took', formatDuration(data.duration_ms)],
              ['Ago', relativeTime(data.started_at, now ?? new Date())],
            ].map(([k, v]) => (
              <div key={k} className="rounded-lg border border-slate-800 bg-slate-950 p-2.5">
                <dt className="text-[10px] text-slate-500">{k}</dt>
                <dd className="mt-0.5 font-semibold text-slate-200">{v}</dd>
              </div>
            ))}
          </dl>

          <div className="grid grid-cols-3 gap-2 rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-center font-mono sm:grid-cols-5">
            {[
              ['Seen', data.seen, 'text-white'],
              ['Created', `+${data.created}`, 'text-emerald-400'],
              ['Updated', `~${data.updated}`, 'text-indigo-400'],
              ['Unchanged', data.unchanged, 'text-slate-300'],
              ['Revived', data.revived, 'text-cyan-400'],
              ['Removed', `−${data.missing}`, 'text-rose-400'],
              ['Model calls', data.summarized, 'text-amber-300'],
              ['Text kept', data.content_stored, 'text-purple-400'],
              ['Text purged', data.content_purged, 'text-amber-400'],
              ['Rejected', data.rejected_count, data.rejected_count ? 'text-amber-400' : 'text-slate-300'],
            ].map(([k, v, tone]) => (
              <div key={k as string}>
                <span className="block text-[10px] text-slate-500">{k}</span>
                <span className={`font-bold ${tone}`}>{v}</span>
              </div>
            ))}
          </div>

          {data.error ? <ErrorNotice message={data.error} /> : null}

          {data.rejected.length ? (
            <div>
              <h3 className="mb-1.5 font-semibold text-slate-300">Documents that could not be catalogued</h3>
              <ul className="max-h-56 space-y-1 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] text-amber-200">
                {data.rejected.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              <p className="mt-1.5 text-[11px] text-slate-500">A page that cannot become an entry is skipped and reported; it never stops the run.</p>
            </div>
          ) : null}

          {data.checkpoint ? (
            <p className="text-[11px] text-slate-500">
              Ended at checkpoint <code className="text-slate-300">{data.checkpoint}</code>
            </p>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
