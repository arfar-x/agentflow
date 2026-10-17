import { AlertTriangle, Eye, FileText, Pencil, Sparkles, Undo2 } from 'lucide-react';
import { useMemo, useState } from 'react';

import { api } from '../../api/api';
import type { FormRecord } from '../../api/types';
import { ErrorNotice } from '../../components/ui/ConfirmDialog';
import { Modal } from '../../components/ui/Modal';
import { Notice } from '../../components/ui/Page';
import { button, input, label } from '../../components/ui/styles';
import { useToast } from '../../components/ui/Toast';
import { formatBytes } from '../../lib/format';
import {
  applyDraft,
  byteLength,
  emptyForm,
  fromRecord,
  isRtl,
  languageName,
  problems,
  slugify,
  toInput,
  type FormState,
} from '../../lib/forms';
import { renderMarkdown } from '../../lib/markdown';

interface Props {
  /** The record being changed; null writes a new one. */
  record: FormRecord | null;
  languages: string[];
  entryTypes: string[];
  summarizerConfigured: boolean;
  maxBytes: number;
  /** A search nobody answered, to start a new record from. */
  seedTitle?: string;
  onClose: () => void;
  onSaved: (record: FormRecord) => void;
}

export function FormEditorModal({ record, languages, entryTypes, summarizerConfigured, maxBytes, seedTitle, onClose, onSaved }: Props) {
  const toast = useToast();
  const isNew = record === null;
  const [form, setForm] = useState<FormState>(() => (record ? fromRecord(record, languages) : emptyForm(languages, seedTitle)));
  const [slugTouched, setSlugTouched] = useState(Boolean(record));
  const tabs = useMemo(() => Object.keys(form.title), [form.title]);
  const [lang, setLang] = useState(tabs[0] ?? 'en');
  const [mode, setMode] = useState<'write' | 'preview'>('write');
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The form as it was before a generated draft was applied, so it can be undone.
  const [beforeDraft, setBeforeDraft] = useState<FormState | null>(null);
  const [draftNote, setDraftNote] = useState<string | null>(null);

  const bytes = byteLength(form.body);
  const found = problems(form, isNew);
  const set = (change: Partial<FormState>) => setForm((current) => ({ ...current, ...change }));

  const setTitle = (language: string, text: string) => {
    const title = { ...form.title, [language]: text };
    // Until the slug is typed by hand, it follows the first title that makes one.
    const slug = slugTouched ? form.slug : slugify(Object.values(title).find((t) => slugify(t)) ?? '');
    set({ title, slug });
  };

  const generate = async () => {
    setDrafting(true);
    setError(null);
    try {
      const result = await api.draftSummary({ title: form.title, body: form.body, type: form.type });
      if (!result.usable) {
        setDraftNote('The model returned nothing usable. Write the summary by hand, or try again.');
        return;
      }
      setBeforeDraft(form);
      setForm(applyDraft(form, result.draft));
      setDraftNote('Drafted by the model — nothing is saved. Read it, correct it, then save.');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setDrafting(false);
    }
  };

  const undoDraft = () => {
    if (beforeDraft) setForm(beforeDraft);
    setBeforeDraft(null);
    setDraftNote(null);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const input = toInput(form);
      const { record: saved } = record ? await api.updateForm(record.slug, input) : await api.createForm(input);
      toast(`Saved “${saved.slug}”. It is queued to publish; it shows in search after the scheduler’s next tick.`);
      onSaved(saved);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const rtl = isRtl(lang);

  return (
    <Modal
      title={isNew ? 'New manual record' : `Edit ${record.slug}`}
      subtitle={isNew ? 'Knowledge that is written nowhere else.' : `Revision ${record.revision}`}
      icon={<FileText className="h-5 w-5" />}
      width="max-w-3xl"
      busy={busy || drafting}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={button.secondary} onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={button.primary} onClick={save} disabled={busy || found.length > 0} title={found[0]}>
            {busy ? 'Saving…' : isNew ? 'Save and publish' : 'Save changes'}
          </button>
        </>
      }
    >
      <div className="space-y-5">
        <Notice tone="amber">
          <span className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              The text and the summary of a record are readable by <strong>every catalog user</strong> through <code>kb_get</code>, whatever
              their access to anything else. Do not put in a secret or a document with a narrower audience.
            </span>
          </span>
        </Notice>

        <div>
          <div role="tablist" aria-label="Language" className="mb-2 flex gap-1">
            {tabs.map((l) => (
              <button
                key={l}
                role="tab"
                aria-selected={lang === l}
                type="button"
                onClick={() => setLang(l)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium ${lang === l ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}
              >
                {languageName(l)} <span className="font-mono opacity-60">{l}</span>
              </button>
            ))}
          </div>
          <label className={label} htmlFor="form-title">
            Title ({lang})
          </label>
          <input
            id="form-title"
            className={input}
            dir={rtl ? 'rtl' : 'ltr'}
            lang={lang}
            value={form.title[lang] ?? ''}
            onChange={(e) => setTitle(lang, e.target.value)}
          />
          <label className={`${label} mt-3`} htmlFor="form-summary">
            Summary ({lang})
          </label>
          <textarea
            id="form-summary"
            rows={3}
            className={input}
            dir={rtl ? 'rtl' : 'ltr'}
            lang={lang}
            value={form.summary[lang] ?? ''}
            onChange={(e) => set({ summary: { ...form.summary, [lang]: e.target.value } })}
            placeholder="What this is, in a sentence or two — it is what search matches against."
          />
          <p className="mt-1 text-[11px] text-slate-500">
            A search in any language reaches a record written in another through the summaries, so write one for each language people use.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <label className={label} htmlFor="form-slug">
              Slug
            </label>
            <input
              id="form-slug"
              className={`${input} font-mono`}
              value={form.slug}
              disabled={!isNew}
              onChange={(e) => {
                setSlugTouched(true);
                set({ slug: e.target.value });
              }}
              placeholder="release-process"
            />
            <p className="mt-1 text-[11px] text-slate-500">{isNew ? 'Names the entry for good.' : 'Fixed once saved.'}</p>
          </div>
          <div>
            <label className={label} htmlFor="form-type">
              Type
            </label>
            <select id="form-type" className={input} value={form.type} onChange={(e) => set({ type: e.target.value })}>
              {entryTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={label} htmlFor="form-owner">
              Owner team
            </label>
            <input id="form-owner" className={input} value={form.ownerTeam} onChange={(e) => set({ ownerTeam: e.target.value })} />
          </div>
          <div>
            <label className={label} htmlFor="form-tags">
              Tags
            </label>
            <input id="form-tags" className={input} value={form.tags} onChange={(e) => set({ tags: e.target.value })} placeholder="comma separated" />
          </div>
          <div>
            <label className={label} htmlFor="form-keywords">
              Keywords
            </label>
            <input id="form-keywords" className={input} value={form.keywords} onChange={(e) => set({ keywords: e.target.value })} placeholder="comma separated" />
          </div>
          <div>
            <label className={label} htmlFor="form-aliases">
              Other names
            </label>
            <input id="form-aliases" className={input} value={form.aliases} onChange={(e) => set({ aliases: e.target.value })} placeholder="what people call it" />
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className={label}>Text</span>
            <div className="flex items-center gap-1">
              <button type="button" className={button.ghost} aria-pressed={mode === 'write'} onClick={() => setMode('write')}>
                <Pencil className="h-3.5 w-3.5" /> Write
              </button>
              <button type="button" className={button.ghost} aria-pressed={mode === 'preview'} onClick={() => setMode('preview')}>
                <Eye className="h-3.5 w-3.5" /> Preview
              </button>
            </div>
          </div>
          {mode === 'write' ? (
            <textarea
              aria-label="Text"
              rows={12}
              className={`${input} font-mono leading-relaxed`}
              dir="auto"
              value={form.body}
              onChange={(e) => set({ body: e.target.value })}
              placeholder="Markdown. This is the document an agent reads."
            />
          ) : (
            <div
              aria-label="Preview"
              dir="auto"
              className="prose-preview max-h-80 min-h-40 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950 p-4 text-xs leading-relaxed text-slate-200"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(form.body) }}
            />
          )}
          <p className={`mt-1 text-[11px] ${bytes > maxBytes ? 'text-rose-300' : 'text-slate-500'}`}>
            {formatBytes(bytes)} of {formatBytes(maxBytes)}
            {bytes > maxBytes ? ' — the rest is cut when the record is kept.' : ''}
          </p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-xs font-semibold text-slate-200">Generate a summary</p>
              <p className="text-[11px] text-slate-500">
                Asks the model to describe the text above. It only drafts: the result fills the fields, you edit it, and nothing is kept until you save.
              </p>
            </div>
            <button
              type="button"
              className={button.secondary}
              onClick={generate}
              disabled={!summarizerConfigured || drafting || !form.body.trim()}
              title={summarizerConfigured ? undefined : 'No summarizer is configured (KB_SUMMARIZER_URL and KB_SUMMARIZER_MODEL).'}
            >
              <Sparkles className="h-3.5 w-3.5" /> {drafting ? 'Drafting…' : 'Generate summary'}
            </button>
          </div>
          {!summarizerConfigured ? (
            <p className="mt-2 text-[11px] text-amber-300">No summarizer is configured, so the summary has to be written by hand.</p>
          ) : null}
          {draftNote ? (
            <p role="status" className="mt-2 flex items-center justify-between gap-2 rounded-lg bg-indigo-500/10 p-2.5 text-[11px] text-indigo-200">
              <span>{draftNote}</span>
              {beforeDraft ? (
                <button type="button" className={button.ghost} onClick={undoDraft}>
                  <Undo2 className="h-3.5 w-3.5" /> Undo
                </button>
              ) : null}
            </p>
          ) : null}
        </div>

        {found.length && (form.body || form.slug || Object.values(form.title).some(Boolean)) ? (
          <ul className="list-disc space-y-0.5 pl-5 text-[11px] text-slate-400">
            {found.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        ) : null}
        {error ? <ErrorNotice message={error} /> : null}
      </div>
    </Modal>
  );
}
