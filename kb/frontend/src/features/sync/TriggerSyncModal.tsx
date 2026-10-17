import { CheckCircle2, Loader2, Play, RefreshCw, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { api } from '../../api/api';
import type { SourceCard, SyncMode, SyncRequestView } from '../../api/types';
import { Modal } from '../../components/ui/Modal';
import { ErrorNotice } from '../../components/ui/ConfirmDialog';
import { StatusBadge } from '../../components/ui/Badge';
import { button, input, label } from '../../components/ui/styles';
import { formatDuration } from '../../lib/format';
import { useResource } from '../../lib/useResource';

const MODES: { id: SyncMode; title: string; text: string }[] = [
  { id: 'incremental', title: 'Incremental', text: 'Only what changed since the last run. Fast, and cannot notice deletions.' },
  { id: 'full', title: 'Full', text: 'Looks at everything. Removes entries the source no longer has, and their stored text.' },
];

interface Props {
  initialSourceId: string;
  onClose: () => void;
  onOpenRun: (id: number) => void;
}

export function TriggerSyncModal({ initialSourceId, onClose, onOpenRun }: Props) {
  const sources = useResource(api.sources, []);
  const [sourceId, setSourceId] = useState(initialSourceId);
  const [mode, setMode] = useState<SyncMode>('incremental');
  const [dryRun, setDryRun] = useState(false);
  const [force, setForce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [requestId, setRequestId] = useState<number | null>(null);

  const list: SourceCard[] = sources.data?.sources ?? [];
  const enabled = list.filter((s) => s.enabled);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const { request_id } = await api.requestSync({ source_id: sourceId, mode, dry_run: dryRun, force: force && !dryRun });
      setRequestId(request_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  if (requestId !== null) {
    return <Progress requestId={requestId} onClose={onClose} onOpenRun={onOpenRun} />;
  }

  return (
    <Modal
      title="Trigger a sync"
      subtitle="Asks the scheduler to run it; you can close this and it carries on."
      icon={<RefreshCw className="h-5 w-5" />}
      onClose={onClose}
      busy={submitting}
      footer={
        <>
          <button type="button" className={button.secondary} onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button type="button" className={button.primary} onClick={submit} disabled={submitting || !sources.data || (sourceId === '*' && enabled.length === 0)}>
            <Play className="h-3.5 w-3.5" /> {submitting ? 'Queuing…' : dryRun ? 'Queue a dry run' : 'Queue the sync'}
          </button>
        </>
      }
    >
      <div className="space-y-5 text-xs">
        <div>
          <label htmlFor="sync-source" className={label}>
            Source
          </label>
          <select id="sync-source" value={sourceId} onChange={(e) => setSourceId(e.target.value)} className={input}>
            <option value="*">All enabled sources{sources.data ? ` (${enabled.length})` : ''}</option>
            {sources.data?.manual ? <option value="form">form (manual records, built in)</option> : null}
            {list.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id} ({s.kind}){s.enabled ? '' : ' — disabled'}
              </option>
            ))}
          </select>
          {sourceId !== '*' && list.find((s) => s.id === sourceId)?.enabled === false ? (
            <p className="mt-1.5 text-[11px] text-amber-300">This source is disabled, so it is not synced on its schedule — but you can run it by hand, for example as a dry run before enabling it.</p>
          ) : null}
        </div>

        <fieldset>
          <legend className={label}>Mode</legend>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {MODES.map((m) => (
              <label
                key={m.id}
                className={`cursor-pointer rounded-xl border p-3 transition ${
                  mode === m.id ? 'border-indigo-500 bg-indigo-600/15' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'
                }`}
              >
                <input type="radio" name="mode" className="sr-only" checked={mode === m.id} onChange={() => setMode(m.id)} />
                <span className="block text-xs font-semibold text-slate-100">{m.title}</span>
                <span className="mt-1 block text-[11px] leading-snug text-slate-400">{m.text}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="space-y-2.5 rounded-xl border border-slate-800 bg-slate-950/60 p-3.5">
          <label className="flex cursor-pointer items-start gap-2.5">
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} className="mt-0.5" />
            <span>
              <span className="font-semibold text-slate-200">Dry run</span>
              <span className="block text-[11px] text-slate-400">Walk the source and report what would change. Writes nothing and makes no model calls.</span>
            </span>
          </label>
          <label className={`flex items-start gap-2.5 ${dryRun ? 'opacity-50' : 'cursor-pointer'}`}>
            <input type="checkbox" checked={force && !dryRun} disabled={dryRun} onChange={(e) => setForce(e.target.checked)} className="mt-0.5" />
            <span>
              <span className="font-semibold text-slate-200">Force re-summarization</span>
              <span className="block text-[11px] text-slate-400">
                Describe every document again, even unchanged ones. One model call each — for recovering entries a since-fixed summarizer left undescribed.
              </span>
            </span>
          </label>
        </div>

        {error ? <ErrorNotice message={error} /> : null}
      </div>
    </Modal>
  );
}

function Progress({ requestId, onClose, onOpenRun }: { requestId: number; onClose: () => void; onOpenRun: (id: number) => void }) {
  const done = useRef(false);
  const requests = useResource(
    () => api.syncRequests(),
    [requestId],
    () => (done.current ? null : 1500),
  );
  const request: SyncRequestView | undefined = requests.data?.requests.find((r) => r.id === requestId);
  useEffect(() => {
    if (request && ['done', 'failed', 'cancelled'].includes(request.status)) done.current = true;
  }, [request]);

  const runs = useResource(
    async () => (request && request.run_ids.length ? Promise.all(request.run_ids.map((id) => api.run(id))) : []),
    [request?.status, request?.run_ids.join(',')],
  );

  const finished = request && ['done', 'failed', 'cancelled'].includes(request.status);

  return (
    <Modal
      title="Sync request"
      icon={<RefreshCw className="h-5 w-5" />}
      onClose={onClose}
      footer={
        <button type="button" className={button.primary} onClick={onClose}>
          {finished ? 'Done' : 'Close — it keeps running'}
        </button>
      }
    >
      <div className="space-y-4 text-xs">
        {!request ? (
          <p className="text-slate-400">Waiting for the request to appear…</p>
        ) : request.status === 'queued' ? (
          <p className="flex items-center gap-2 text-slate-300">
            <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
            Queued — {request.position && request.position > 1 ? `${request.position - 1} ahead of it` : 'next in line'}. The scheduler picks requests up every few seconds, one at a time.
          </p>
        ) : request.status === 'running' ? (
          <p className="flex items-center gap-2 text-slate-300">
            <Loader2 className="h-4 w-4 animate-spin text-blue-400" />
            The scheduler is running it.
          </p>
        ) : request.status === 'done' ? (
          <p className="flex items-center gap-2 font-semibold text-emerald-300">
            <CheckCircle2 className="h-4 w-4" /> Finished.
          </p>
        ) : (
          <p className="flex items-center gap-2 font-semibold text-rose-300">
            <XCircle className="h-4 w-4" /> {request.status === 'cancelled' ? 'Cancelled.' : 'Failed.'}
          </p>
        )}

        {request?.error ? <ErrorNotice message={request.error} /> : null}

        {runs.data?.length ? (
          <ul className="space-y-2">
            {runs.data.map((run) => (
              <li key={run.id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold text-slate-100">{run.source_id}</span>
                  <span className="flex items-center gap-2">
                    <StatusBadge status={run.status} />
                    <span className="text-slate-500">{formatDuration(run.duration_ms)}</span>
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-center font-mono sm:grid-cols-6">
                  {[
                    ['Seen', run.seen],
                    ['Created', run.created],
                    ['Updated', run.updated],
                    ['Removed', run.missing],
                    ['Text kept', run.content_stored],
                    ['Rejected', run.rejected_count],
                  ].map(([name, value]) => (
                    <div key={name as string} className="rounded bg-slate-900 p-1.5">
                      <span className="block text-[10px] text-slate-500">{name}</span>
                      <span className="font-bold text-slate-200">{value}</span>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className="mt-2 text-[11px] font-medium text-indigo-400 hover:text-indigo-300"
                  onClick={() => {
                    onOpenRun(run.id);
                    onClose();
                  }}
                >
                  Open the full report →
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Modal>
  );
}
