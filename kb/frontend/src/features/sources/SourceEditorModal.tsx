import { Plus, Sliders, Trash2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { api } from '../../api/api';
import type { ChangePreview, MechanismSettings, SourceCard, SourceChange, SourceKind, SourcesResponse } from '../../api/types';
import { ErrorNotice } from '../../components/ui/ConfirmDialog';
import { Modal } from '../../components/ui/Modal';
import { Toggle } from '../../components/ui/Toggle';
import { button, input, label } from '../../components/ui/styles';
import {
  MECHANISMS,
  blankForm,
  blankProject,
  blankScope,
  formFromCard,
  formToConfig,
  problems,
  type MechanismName,
  type SourceForm,
  type Tri,
} from '../../lib/sourceForm';
import { formatBytes } from '../../lib/format';

const KIND_LABEL: Record<SourceKind, string> = { confluence: 'Confluence wiki', jira: 'Jira issues', gitlab: 'GitLab repositories' };

const MECH_LABEL: Record<MechanismName, { title: string; text: string }> = {
  incremental: { title: 'Incremental', text: 'Asks only for what changed since the last run.' },
  full_scrape: { title: 'Nightly full pass', text: 'Looks at everything; the only run that notices deletions.' },
  webhook: { title: 'Webhook', text: 'Refreshes a file within seconds of a push (GitLab only; needs the receiver running).' },
  lazy_refresh: { title: 'Refresh on read', text: 'Refreshes an entry when someone reads it after it went stale.' },
};

interface Props {
  /** The source to edit; none to add a new one. */
  source: SourceCard | null;
  data: SourcesResponse;
  onPreview: (change: SourceChange, preview: ChangePreview) => void;
  onClose: () => void;
}

export function SourceEditorModal({ source, data, onPreview, onClose }: Props) {
  const mode = source ? 'update' : 'create';
  const [form, setForm] = useState<SourceForm>(() => (source ? formFromCard(source) : blankForm('confluence')));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const defaults = data.defaults?.mechanisms ?? {};

  const set = <K extends keyof SourceForm>(key: K, value: SourceForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const setMech = (name: MechanismName, patch: Partial<SourceForm['mechanisms'][MechanismName]>) =>
    setForm((f) => ({ ...f, mechanisms: { ...f.mechanisms, [name]: { ...f.mechanisms[name], ...patch } } }));
  const found = problems(form, mode);

  const review = async () => {
    setBusy(true);
    setError(null);
    const config = formToConfig(form, mode);
    const change: SourceChange = source ? { action: 'update', id: source.id, config } : { action: 'create', config };
    try {
      onPreview(change, await api.previewSource(change));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const defaultOf = (name: MechanismName, key: keyof MechanismSettings): string => {
    const value = defaults[name]?.[key];
    return value === undefined ? '' : String(value);
  };

  return (
    <Modal
      title={source ? `Edit ${source.id}` : 'Add a source'}
      subtitle="Nothing is written until you have reviewed the change."
      icon={<Sliders className="h-5 w-5" />}
      width="max-w-2xl"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" className={button.secondary} onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={button.primary} onClick={review} disabled={busy || found.length > 0}>
            {busy ? 'Checking…' : 'Review the change…'}
          </button>
        </>
      }
    >
      <div className="space-y-6 text-xs">
        {/* identity */}
        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="src-id" className={label}>
              Id
            </label>
            <input
              id="src-id"
              className={input}
              value={form.id}
              onChange={(e) => set('id', e.target.value)}
              disabled={mode === 'update'}
              placeholder="confluence-eng"
              autoComplete="off"
            />
            <p className="mt-1 text-[11px] text-slate-500">
              {mode === 'update' ? 'An id cannot change: every entry is named after it.' : 'Becomes part of every entry id, so choose it once.'}
            </p>
          </div>
          <div>
            <label htmlFor="src-kind" className={label}>
              Kind
            </label>
            <select
              id="src-kind"
              className={input}
              value={form.kind}
              onChange={(e) => setForm({ ...blankForm(e.target.value as SourceKind), id: form.id })}
              disabled={mode === 'update'}
            >
              {(Object.keys(KIND_LABEL) as SourceKind[]).map((k) => (
                <option key={k} value={k}>
                  {KIND_LABEL[k]}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/60 p-3 sm:col-span-2">
            <div>
              <span className="font-semibold text-slate-200">Enabled</span>
              <p className="text-[11px] text-slate-500">A disabled source is not synced on its schedule. You can still run it by hand — a dry run first is a good idea.</p>
            </div>
            <Toggle checked={form.enabled} onChange={(v) => set('enabled', v)} label="Enabled" />
          </div>
        </section>

        {/* what it reads */}
        <section className="space-y-3">
          <h3 className="text-sm font-semibold text-slate-200">What it reads</h3>
          {form.kind === 'confluence' ? (
            <>
              <Field id="spaces" title="Spaces" hint="Space keys, separated by commas.">
                <input id="spaces" className={input} value={form.spaces} onChange={(e) => set('spaces', e.target.value)} placeholder="ENG, PRODUCT" />
              </Field>
              <Field id="exclude" title="Skip pages labelled" hint="Pages with any of these labels are left out.">
                <input id="exclude" className={input} value={form.excludeLabels} onChange={(e) => set('excludeLabels', e.target.value)} placeholder="archive, draft" />
              </Field>
              <PairList
                title="Label decides type"
                hint="A page with the label becomes an entry of that type (doc, spec, product, system, team, person, term)."
                pairs={form.labelTypes}
                onChange={(pairs) => set('labelTypes', pairs)}
              />
            </>
          ) : null}
          {form.kind === 'jira' ? (
            <Field id="jql" title="JQL" hint="Most issues are work items, not knowledge: keep the query narrow.">
              <textarea id="jql" className={`${input} font-mono`} rows={3} value={form.jql} onChange={(e) => set('jql', e.target.value)} placeholder="project in (PAY, CHK) AND issuetype = Epic" />
            </Field>
          ) : null}
          {form.kind === 'gitlab' ? (
            <>
              <Field id="base" title="Base URL" hint="May be a ${VAR} placeholder, resolved by the scheduler.">
                <input id="base" className={input} value={form.baseUrl} onChange={(e) => set('baseUrl', e.target.value)} placeholder="https://gitlab.example.com" />
              </Field>
              <Field id="tokenenv" title="Token variable" hint="The NAME of the environment variable that holds a read-only token — never the token itself. The value lives in .env.">
                <input id="tokenenv" className={`${input} font-mono`} value={form.tokenEnv} onChange={(e) => set('tokenEnv', e.target.value)} />
              </Field>
              <Projects form={form} onChange={(projects) => set('projects', projects)} />
            </>
          ) : null}
        </section>

        {/* when */}
        <section className="space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-200">When it runs</h3>
            <p className="text-[11px] text-slate-500">&ldquo;Inherit&rdquo; follows the defaults in the file, so changing them later changes this source too.</p>
          </div>
          {MECHANISMS.filter((name) => name !== 'webhook' || form.kind === 'gitlab').map((name) => {
            const m = form.mechanisms[name];
            const param = name === 'incremental' ? 'every' : name === 'full_scrape' ? 'at' : name === 'lazy_refresh' ? 'stale_after' : null;
            return (
              <div key={name} className="grid grid-cols-1 items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3 sm:grid-cols-[1fr_auto_auto]">
                <div>
                  <span className="font-semibold text-slate-200">{MECH_LABEL[name].title}</span>
                  <p className="text-[11px] text-slate-500">{MECH_LABEL[name].text}</p>
                </div>
                <select aria-label={`${MECH_LABEL[name].title}: on or off`} className={`${input} w-40`} value={m.enabled} onChange={(e) => setMech(name, { enabled: e.target.value as Tri })}>
                  <option value="inherit">Inherit{defaults[name]?.enabled === undefined ? '' : defaults[name]?.enabled ? ' (on)' : ' (off)'}</option>
                  <option value="on">On</option>
                  <option value="off">Off</option>
                </select>
                {param ? (
                  <input
                    aria-label={`${MECH_LABEL[name].title}: ${param === 'every' ? 'how often' : param === 'at' ? 'time of day' : 'stale after'}`}
                    className={`${input} w-32 font-mono`}
                    value={m[param]}
                    onChange={(e) => setMech(name, { [param]: e.target.value })}
                    placeholder={defaultOf(name, param) || (param === 'every' ? '15m' : param === 'at' ? '03:00' : '24h')}
                  />
                ) : (
                  <span />
                )}
              </div>
            );
          })}
        </section>

        {/* the text policy */}
        <section className={`space-y-3 rounded-xl border p-4 ${form.storeRaw ? 'border-purple-500/40 bg-purple-500/5' : 'border-slate-800 bg-slate-950/60'}`}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-200">Keep each document&apos;s text</h3>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
                Lets agents read the document through the catalog, not only its summary. It makes the full text readable by <strong>every user of the catalog</strong>, whatever their
                access to the original. Turn it on only for content anyone here may read. Off by default, and per source on purpose.
              </p>
            </div>
            <Toggle checked={form.storeRaw} onChange={(v) => set('storeRaw', v)} label="Keep each document's text" tone="purple" />
          </div>
          {form.storeRaw ? (
            <Field id="cap" title="Largest document kept" hint={`In bytes, at least 1024. Blank uses the default (${formatBytes(data.defaults?.raw_content_max_bytes ?? 10485760)}). Larger documents keep their start, marked truncated.`}>
              <input id="cap" className={`${input} font-mono`} inputMode="numeric" value={form.maxBytes} onChange={(e) => set('maxBytes', e.target.value)} placeholder={String(data.defaults?.raw_content_max_bytes ?? 10485760)} />
            </Field>
          ) : null}
        </section>

        {found.length ? (
          <ul className="space-y-1 text-[11px] text-amber-300">
            {found.map((problem) => (
              <li key={problem}>• {problem}</li>
            ))}
          </ul>
        ) : null}
        {error ? <ErrorNotice message={error} /> : null}
      </div>
    </Modal>
  );
}

function Field({ id, title, hint, children }: { id: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className={label}>
        {title}
      </label>
      {children}
      {hint ? <p className="mt-1 text-[11px] text-slate-500">{hint}</p> : null}
    </div>
  );
}

function PairList({ title, hint, pairs, onChange }: { title: string; hint: string; pairs: { label: string; type: string }[]; onChange: (p: { label: string; type: string }[]) => void }) {
  return (
    <div>
      <span className={label}>{title}</span>
      <div className="space-y-1.5">
        {pairs.map((pair, index) => (
          // eslint-disable-next-line react/no-array-index-key
          <div key={index} className="flex gap-2">
            <input aria-label="Label" className={`${input} font-mono`} value={pair.label} placeholder="kb-glossary" onChange={(e) => onChange(pairs.map((p, i) => (i === index ? { ...p, label: e.target.value } : p)))} />
            <span className="self-center text-slate-500">→</span>
            <input aria-label="Entry type" className={`${input} font-mono`} value={pair.type} placeholder="term" onChange={(e) => onChange(pairs.map((p, i) => (i === index ? { ...p, type: e.target.value } : p)))} />
            <button type="button" className={button.icon} aria-label="Remove" onClick={() => onChange(pairs.filter((_, i) => i !== index))}>
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
      <button type="button" className={`${button.ghost} mt-1.5`} onClick={() => onChange([...pairs, { label: '', type: '' }])}>
        <Plus className="h-3.5 w-3.5" /> Add a label
      </button>
      <p className="mt-1 text-[11px] text-slate-500">{hint}</p>
    </div>
  );
}

function Projects({ form, onChange }: { form: SourceForm; onChange: (p: SourceForm['projects']) => void }) {
  const update = (index: number, patch: Partial<SourceForm['projects'][number]>) => onChange(form.projects.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  return (
    <div className="space-y-3">
      <span className={label}>Projects</span>
      {form.projects.map((project, pi) => (
        // eslint-disable-next-line react/no-array-index-key
        <div key={pi} className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          <div className="flex gap-2">
            <input aria-label="Project path" className={`${input} font-mono`} value={project.path} placeholder="group/project" onChange={(e) => update(pi, { path: e.target.value })} />
            <input aria-label="Branch" className={`${input} w-40 font-mono`} value={project.ref} placeholder="default branch" onChange={(e) => update(pi, { ref: e.target.value })} />
            <button type="button" className={button.icon} aria-label="Remove project" onClick={() => onChange(form.projects.filter((_, i) => i !== pi))}>
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
          {project.scopes.map((scope, si) => (
            // eslint-disable-next-line react/no-array-index-key
            <div key={si} className="grid grid-cols-2 gap-2 rounded-md bg-slate-900 p-2.5 sm:grid-cols-5">
              {(
                [
                  ['dir', 'Directory', '.'],
                  ['patterns', 'Files', '**/*.md'],
                  ['exclude', 'Skip', '**/CHANGELOG.md'],
                  ['type', 'Entry type', 'spec'],
                  ['tags', 'Tags', 'adr'],
                ] as const
              ).map(([key, title, placeholder]) => (
                <input
                  key={key}
                  aria-label={`${title} (scope ${si + 1} of ${project.path || 'project'})`}
                  className={`${input} font-mono`}
                  value={scope[key]}
                  placeholder={`${title}: ${placeholder}`}
                  onChange={(e) => update(pi, { scopes: project.scopes.map((s, i) => (i === si ? { ...s, [key]: e.target.value } : s)) })}
                />
              ))}
            </div>
          ))}
          <button type="button" className={button.ghost} onClick={() => update(pi, { scopes: [...project.scopes, blankScope()] })}>
            <Plus className="h-3.5 w-3.5" /> Limit to a directory
          </button>
        </div>
      ))}
      <button type="button" className={button.ghost} onClick={() => onChange([...form.projects, blankProject()])}>
        <Plus className="h-3.5 w-3.5" /> Add a project
      </button>
    </div>
  );
}
