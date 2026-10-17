import { AlertTriangle, Database, HardDrive, History, Pause, Pencil, Play, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { api } from '../../api/api';
import type { ChangePreview, SourceCard, SourceChange } from '../../api/types';
import { useConsole } from '../../components/ConsoleContext';
import { Badge, StatusDot, runTone } from '../../components/ui/Badge';
import { ConfirmDialog } from '../../components/ui/ConfirmDialog';
import { Empty, Loading, LoadError, Notice, Page } from '../../components/ui/Page';
import { button, card, input } from '../../components/ui/styles';
import { useToast } from '../../components/ui/Toast';
import { formatBytes, relativeTime } from '../../lib/format';
import { credentialsMissing, describeMechanisms, describeSettings } from '../../lib/sources';
import { useResource } from '../../lib/useResource';
import { ApprovalDialog } from './ApprovalDialog';
import { RevisionsModal } from './RevisionsModal';
import { SourceEditorModal } from './SourceEditorModal';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'enabled', label: 'Enabled' },
  { id: 'disabled', label: 'Disabled' },
  { id: 'text', label: 'Stores text' },
] as const;

export function SourcesView({
  onNavigate,
  focusId,
}: {
  onNavigate: (name: string, param?: string | number | null) => void;
  /** Open this source's editor on arrival (the schedule's "change cadence"). */
  focusId?: string | null;
}) {
  const { openTrigger, overview } = useConsole();
  const toast = useToast();
  const sources = useResource(api.sources, [], 15000);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]['id']>('all');
  const [search, setSearch] = useState('');
  const [purging, setPurging] = useState<SourceCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  const [editing, setEditing] = useState<SourceCard | 'new' | null>(null);
  const [pending, setPending] = useState<{ change: SourceChange; preview: ChangePreview } | null>(null);
  const [history, setHistory] = useState(false);
  const [retiring, setRetiring] = useState<{ source_id: string; entries: number } | null>(null);
  // Deleting a source leaves its entries searchable; offer to retire them with it.
  const [alsoRetire, setAlsoRetire] = useState(true);
  const retireAfter = useRef<{ id: string; entries: number } | null>(null);
  const { overview: consoleOverview } = useConsole();

  const focused = useRef<string | null>(null);
  if (focusId && sources.data && focused.current !== focusId) {
    focused.current = focusId;
    const target = sources.data.sources.find((s) => s.id === focusId);
    if (target && sources.data.editable) queueMicrotask(() => setEditing(target));
    queueMicrotask(() => onNavigate('sources'));
  }

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (sources.data?.sources ?? []).filter((s) => {
      if (filter === 'enabled' && !s.enabled) return false;
      if (filter === 'disabled' && s.enabled) return false;
      if (filter === 'text' && !s.store_raw_content) return false;
      if (!q) return true;
      return (
        s.id.toLowerCase().includes(q) ||
        s.kind.toLowerCase().includes(q) ||
        JSON.stringify(s.settings).toLowerCase().includes(q)
      );
    });
  }, [sources.data, filter, search]);

  const startChange = async (change: SourceChange, retire?: { id: string; entries: number }) => {
    try {
      const preview = await api.previewSource(change);
      retireAfter.current = retire ?? null;
      setAlsoRetire(true);
      setPending({ change, preview });
    } catch (err) {
      toast((err as Error).message, 'error');
    }
  };

  const applied = async () => {
    const retire = alsoRetire ? retireAfter.current : null;
    setPending(null);
    setEditing(null);
    toast('Written to kb-sources.yaml. The scheduler reads it on its next tick.');
    if (retire) {
      try {
        await api.retireSource(retire.id);
        toast(`Removing ${retire.entries} entries of ${retire.id} from the catalog — queued for the scheduler.`);
      } catch (err) {
        toast((err as Error).message, 'error');
      }
    }
    sources.reload();
    consoleOverview.reload();
  };

  const retire = async () => {
    if (!retiring) return;
    setBusy(true);
    try {
      await api.retireSource(retiring.source_id);
      toast(`Removing ${retiring.entries} entries of ${retiring.source_id} — queued for the scheduler.`);
      setRetiring(null);
      sources.reload();
    } catch (err) {
      toast((err as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const purge = async () => {
    if (!purging) return;
    setBusy(true);
    setPurgeError(null);
    try {
      await api.purgeContent(purging.id);
      toast(`Purge of ${purging.id}'s stored text queued.`);
      setPurging(null);
    } catch (err) {
      setPurgeError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!sources.data) return sources.error ? <LoadError error={sources.error} onRetry={sources.reload} /> : <Loading />;
  // The server's clock, so a laptop with the wrong time cannot make a run look stale.
  const now = overview.data?.now ?? new Date().toISOString();

  return (
    <Page
      title="Sources"
      badge={<Badge tone="indigo">{sources.data.sources.length} total</Badge>}
      description={
        <>
          What the catalog reads, from <code className="text-indigo-300">config/kb-sources.yaml</code>. Whether a source keeps its documents&apos; text is decided per source, never as a default.
        </>
      }
      onRefresh={sources.reload}
      actions={
        <>
          {sources.data.approved ? (
            <button
              type="button"
              className={sources.data.approved.value ? button.secondary : button.warning}
              disabled={!sources.data.editable}
              onClick={() => startChange({ action: 'approved', value: !sources.data!.approved!.value })}
              title={sources.data.approved.value ? 'Stop every source syncing' : 'Syncing is off for every source'}
            >
              {sources.data.approved.value ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              {sources.data.approved.value ? 'Syncing on' : 'Syncing off — switch on…'}
            </button>
          ) : null}
          <button type="button" className={button.secondary} onClick={() => setHistory(true)}>
            <History className="h-3.5 w-3.5" /> History
          </button>
          <button type="button" className={button.primary} disabled={!sources.data.editable} onClick={() => setEditing('new')}>
            <Plus className="h-3.5 w-3.5" /> Add a source
          </button>
        </>
      }
    >
      {!sources.data.editable && !sources.data.config_error ? (
        <Notice tone="amber">
          The console cannot write <code>config/kb-sources.yaml</code> (it is mounted read-only, or owned by a user the console does not run as — see <code>KB_ADMIN_USER</code>). Edit the file by hand; this page still shows it.
        </Notice>
      ) : null}
      {sources.data.config_error ? <Notice tone="rose">The source configuration cannot be read: {sources.data.config_error}</Notice> : null}

      <div className="flex flex-col justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-3 sm:flex-row sm:items-center">
        <div className="scrollbar-none flex items-center gap-1.5 overflow-x-auto">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                filter === f.id ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="relative min-w-60">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search sources…"
            aria-label="Search sources"
            className={`${input} pl-9`}
          />
        </div>
      </div>

      {shown.length === 0 ? (
        <Empty>No source matches. Sources are added to <code>config/kb-sources.yaml</code> (or discovered with <code>make kb-sources-discover</code>).</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {shown.map((source) => (
            <SourceCardView
              key={source.id}
              source={source}
              now={now}
              editable={Boolean(sources.data?.editable)}
              onSync={() => openTrigger(source.id)}
              onPurge={() => setPurging(source)}
              onRun={(id) => onNavigate('reports', id)}
              onEdit={() => setEditing(source)}
              onDelete={() => startChange({ action: 'delete', id: source.id }, { id: source.id, entries: source.entries })}
            />
          ))}
        </div>
      )}

      {sources.data.orphans.length ? (
        <div className={`${card} p-5`}>
          <h2 className="mb-1 text-sm font-semibold text-slate-200">Entries from sources that are no longer configured</h2>
          <p className="mb-3 text-xs text-slate-400">
            Nothing syncs these any more, so nothing retires them: they stay searchable and go stale. Retiring hides them from search (it is reversible — they come back if the source does).
          </p>
          <ul className="divide-y divide-slate-800/80">
            {sources.data.orphans.map((orphan) => (
              <li key={orphan.source_id} className="flex items-center justify-between py-2.5 text-xs">
                <span>
                  <span className="font-semibold text-slate-100">{orphan.source_id}</span> <Badge tone="slate">{orphan.entries} entries</Badge>
                </span>
                <button type="button" className={button.secondary} onClick={() => setRetiring(orphan)}>
                  Remove from the catalog…
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {editing ? (
        <SourceEditorModal
          source={editing === 'new' ? null : editing}
          data={sources.data}
          onClose={() => setEditing(null)}
          onPreview={(change, preview) => {
            retireAfter.current = null;
            setPending({ change, preview });
          }}
        />
      ) : null}

      {pending ? (
        <ApprovalDialog
          change={pending.change}
          preview={pending.preview}
          onApplied={applied}
          onClose={() => setPending(null)}
          extra={
            pending.change.action === 'delete' && retireAfter.current ? (
              <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-100">
                <input type="checkbox" checked={alsoRetire} onChange={(e) => setAlsoRetire(e.target.checked)} className="mt-0.5" />
                <span>
                  Also remove its {retireAfter.current.entries} entries from search (recommended). Reversible: it hides them, it does not erase them.
                </span>
              </label>
            ) : null
          }
        />
      ) : null}

      {history ? <RevisionsModal onClose={() => setHistory(false)} /> : null}

      {retiring ? (
        <ConfirmDialog
          title="Remove these entries from the catalog?"
          confirmLabel="Remove from search"
          busy={busy}
          onConfirm={retire}
          onCancel={() => setRetiring(null)}
        >
          <p>
            The {retiring.entries} entries from <strong>{retiring.source_id}</strong> stop appearing in search, and any stored text goes with them. They are kept, marked as removed at the source, and come back if a source with this id is synced again.
          </p>
        </ConfirmDialog>
      ) : null}

      {purging ? (
        <ConfirmDialog
          title="Purge stored text?"
          confirmLabel="Queue the purge"
          busy={busy}
          error={purgeError}
          onConfirm={purge}
          onCancel={() => setPurging(null)}
        >
          <p>
            This removes the {purging.content.documents} stored document text(s) of <strong>{purging.id}</strong> ({formatBytes(purging.content.text_bytes)}). Their catalog entries and summaries stay.
          </p>
          {purging.store_raw_content ? (
            <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-200">
              This source is still set to store text, so the <em>next sync puts it back</em>. To stop keeping it, turn <code>store_raw_content</code> off in the source configuration — the next sync then removes it for good.
            </p>
          ) : null}
        </ConfirmDialog>
      ) : null}
    </Page>
  );
}

function SourceCardView({
  source,
  now,
  editable,
  onSync,
  onPurge,
  onRun,
  onEdit,
  onDelete,
}: {
  source: SourceCard;
  now: string;
  editable: boolean;
  onSync: () => void;
  onPurge: () => void;
  onRun: (id: number) => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const missing = credentialsMissing(source);
  const last = source.last_run;
  return (
    <article className={`${card} flex flex-col justify-between p-5 transition hover:border-slate-700`}>
      <div>
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-bold text-slate-100">{source.id}</h2>
              <Badge tone="indigo">{source.kind}</Badge>
              {source.enabled ? <Badge tone="emerald">enabled</Badge> : <Badge tone="amber">disabled</Badge>}
              <Badge tone="slate" title={source.origin === 'ui' ? 'Added from this console' : 'Declared in the configuration file'}>
                {source.origin === 'ui' ? 'added here' : 'in file'}
              </Badge>
            </div>
          </div>
        </div>

        <dl className="mt-3.5 space-y-1.5 rounded-lg border border-slate-800/60 bg-slate-950/70 p-2.5 font-mono text-[11px]">
          {describeSettings(source).map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <dt className="shrink-0 text-slate-500">{k}:</dt>
              <dd className="break-all text-slate-300">{v}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {describeMechanisms(source.mechanisms).map((m) => (
            <span
              key={m.name}
              className={`rounded-md border px-2 py-0.5 text-[10px] ${
                m.on ? 'border-slate-700 bg-slate-800/80 text-slate-200' : 'border-slate-800 text-slate-600 line-through'
              }`}
            >
              {m.name}
              {m.on && m.detail ? ` · ${m.detail}` : ''}
            </span>
          ))}
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
          <div className="rounded-lg border border-slate-800/60 bg-slate-950/50 p-2.5">
            <span className="block text-[11px] text-slate-500">Document text</span>
            <div className={`mt-1 flex items-center gap-1.5 font-semibold ${source.store_raw_content ? 'text-purple-400' : 'text-slate-500'}`}>
              <HardDrive className="h-3.5 w-3.5" />
              {source.store_raw_content ? 'Stored' : 'Summary only'}
            </div>
            <span className="mt-0.5 block text-[10px] text-slate-400">
              {source.store_raw_content ? `Cap ${formatBytes(source.raw_content_max_bytes)} per document` : 'Agents follow the link to read it'}
            </span>
          </div>
          <div className="rounded-lg border border-slate-800/60 bg-slate-950/50 p-2.5">
            <span className="block text-[11px] text-slate-500">In the catalog</span>
            <div className="mt-1 flex items-center gap-1.5 font-semibold text-slate-200">
              <Database className="h-3.5 w-3.5 text-cyan-400" />
              {source.entries} entries
            </div>
            <span className="mt-0.5 block text-[10px] text-slate-400">
              {source.content.documents ? `${formatBytes(source.content.text_bytes)} of text` : 'no text stored'}
            </span>
          </div>
        </div>

        {source.store_raw_content ? (
          <p className="mt-2.5 text-[11px] text-purple-300/80">Every catalog user can read this text, whatever their access in the source system.</p>
        ) : null}

        {source.enabled && missing.length ? (
          <p className="mt-2.5 flex gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-[11px] text-amber-200">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              The scheduler cannot see: <span className="font-mono">{missing.join(', ')}</span>. Set it in <code>.env</code>; syncs of this source will fail until then.
            </span>
          </p>
        ) : null}
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-slate-800/80 pt-3 text-xs">
        <div className="text-[11px] text-slate-400">
          {last ? (
            <button type="button" onClick={() => onRun(last.id)} className="inline-flex items-center gap-1.5 hover:text-slate-200">
              <StatusDot tone={runTone(last.status)} />
              <span className="capitalize">{last.status}</span>
              <span>·</span>
              <span>{last.finished_at ? relativeTime(last.finished_at, now) : 'in progress'}</span>
            </button>
          ) : (
            <span className="inline-flex items-center gap-1.5">
              <StatusDot tone="idle" /> Never synced
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {source.content.documents > 0 ? (
            <button type="button" onClick={onPurge} className="inline-flex items-center gap-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-300 hover:bg-amber-500/20">
              <Trash2 className="h-3 w-3" /> Purge text
            </button>
          ) : null}
          <button type="button" className={button.icon} onClick={onSync} title="Sync this source" aria-label={`Sync ${source.id}`}>
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button type="button" className={button.icon} onClick={onEdit} disabled={!editable} title="Edit this source" aria-label={`Edit ${source.id}`}>
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button type="button" className={`${button.icon} hover:!bg-rose-900/40 hover:!text-rose-400`} onClick={onDelete} disabled={!editable} title="Delete this source" aria-label={`Delete ${source.id}`}>
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </article>
  );
}
