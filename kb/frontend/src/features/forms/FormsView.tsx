import { BookOpen, FileText, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { api } from '../../api/api';
import type { FormRecord } from '../../api/types';
import { useConsole } from '../../components/ConsoleContext';
import { Badge } from '../../components/ui/Badge';
import { ConfirmDialog } from '../../components/ui/ConfirmDialog';
import { Empty, Loading, LoadError, Notice, Page } from '../../components/ui/Page';
import { button, card, input } from '../../components/ui/styles';
import { useToast } from '../../components/ui/Toast';
import { relativeTime } from '../../lib/format';
import { parseFormsParam, recordTitle } from '../../lib/forms';
import { useResource } from '../../lib/useResource';
import { ContentInspector } from './ContentInspector';
import { FormEditorModal } from './FormEditorModal';

export function FormsView({
  param,
  onNavigate,
}: {
  /** "new" or "new:<a search nobody answered>" opens a blank record on arrival. */
  param: string | null;
  onNavigate: (name: string, param?: string | number | null) => void;
}) {
  const toast = useToast();
  const { overview, config } = useConsole();
  // A record waiting for the scheduler becomes published without any action
  // here, so poll briskly while anything is pending.
  const forms = useResource(api.forms, [], (data) => (data?.records.some((r) => r.publication === 'pending') ? 3000 : 15000));
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<{ record: FormRecord | null; seed?: string } | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<FormRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const arrived = useRef<string | null>(null);
  const seed = parseFormsParam(param);
  if (seed && forms.data && arrived.current !== param) {
    arrived.current = param;
    queueMicrotask(() => {
      setEditing({ record: null, seed: seed.seed });
      onNavigate('forms');
    });
  }

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    const languages = forms.data?.languages ?? [];
    return (forms.data?.records ?? []).filter(
      (r) =>
        !q ||
        r.slug.includes(q) ||
        recordTitle(r, languages).toLowerCase().includes(q) ||
        r.tags.some((t) => t.toLowerCase().includes(q)) ||
        (r.owner_team ?? '').toLowerCase().includes(q),
    );
  }, [forms.data, search]);

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    setDeleteError(null);
    try {
      await api.deleteForm(deleting.slug);
      toast(`Deleted “${deleting.slug}”. It leaves search when the scheduler next syncs.`);
      setDeleting(null);
      forms.reload();
    } catch (err) {
      setDeleteError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!forms.data) return forms.error ? <LoadError error={forms.error} onRetry={forms.reload} /> : <Loading />;
  const data = forms.data;
  const now = overview.data?.now ?? new Date().toISOString();

  return (
    <Page
      title="Form records"
      badge={<Badge tone="indigo">{data.records.length} total</Badge>}
      description="Knowledge written here rather than found in a wiki or a repository: a decision nobody recorded, a team’s glossary. It joins the catalog through the same sync as everything else."
      onRefresh={forms.reload}
      actions={
        <button type="button" className={button.primary} onClick={() => setEditing({ record: null })}>
          <Plus className="h-3.5 w-3.5" /> New record
        </button>
      }
    >
      {!data.summarizer_configured ? (
        <Notice tone="amber">
          No summarizer is configured, so summaries are written by hand. A record without one is still findable by its title and keywords.
        </Notice>
      ) : null}

      <div className="relative min-w-60 max-w-sm">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search records…" aria-label="Search records" className={`${input} pl-9`} />
      </div>

      {shown.length === 0 ? (
        <Empty>
          {data.records.length ? 'No record matches.' : 'No manual records yet. Add one for knowledge that is written down nowhere else.'}
        </Empty>
      ) : (
        <div className={`${card} overflow-x-auto`}>
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">Record</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Owner</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Updated</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/80">
              {shown.map((record) => (
                <tr key={record.slug} className="align-top">
                  <td className="px-4 py-3">
                    <div className="font-semibold text-slate-100" dir="auto">
                      {recordTitle(record, data.languages)}
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-slate-500">{record.slug}</div>
                    {Object.keys(record.summary).length === 0 ? (
                      <div className="mt-1">
                        <Badge tone="amber" title="Findable by its title and keywords only">
                          no summary
                        </Badge>
                      </div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone="slate">{record.type}</Badge>
                  </td>
                  <td className="px-4 py-3 text-slate-300">{record.owner_team ?? '—'}</td>
                  <td className="px-4 py-3">
                    {record.publication === 'published' ? (
                      <Badge tone="emerald">published</Badge>
                    ) : (
                      <Badge tone="amber" title="Saved. The scheduler has not caught up yet.">
                        waiting for the scheduler
                      </Badge>
                    )}
                    <span className="ml-2 text-slate-500">rev {record.revision}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{relativeTime(record.updated_at, now)}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1.5">
                      <button type="button" className={button.icon} title="Read as an agent" aria-label={`Read ${record.slug} as an agent`} onClick={() => setReading(record.slug)}>
                        <BookOpen className="h-3.5 w-3.5" />
                      </button>
                      <button type="button" className={button.icon} title="Edit" aria-label={`Edit ${record.slug}`} onClick={() => setEditing({ record })}>
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button type="button" className={button.icon} title="Delete" aria-label={`Delete ${record.slug}`} onClick={() => setDeleting(record)}>
                        <Trash2 className="h-3.5 w-3.5 text-rose-400" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing ? (
        <FormEditorModal
          record={editing.record}
          seedTitle={editing.seed}
          languages={data.languages}
          entryTypes={config?.entry_types ?? ['doc']}
          summarizerConfigured={data.summarizer_configured}
          maxBytes={data.max_bytes}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            forms.reload();
            overview.reload();
          }}
        />
      ) : null}

      {reading ? <ContentInspector slug={reading} onClose={() => setReading(null)} /> : null}

      {deleting ? (
        <ConfirmDialog
          title="Delete this record?"
          confirmLabel="Delete the record"
          tone="danger"
          busy={busy}
          error={deleteError}
          onConfirm={remove}
          onCancel={() => setDeleting(null)}
        >
          <p className="flex items-start gap-2">
            <FileText className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
            <span>
              <strong>{recordTitle(deleting, data.languages)}</strong> (<code>{deleting.slug}</code>) is removed. It stays searchable until the
              scheduler’s next sync, which then retires its entry and the text kept for it.
            </span>
          </p>
        </ConfirmDialog>
      ) : null}
    </Page>
  );
}
