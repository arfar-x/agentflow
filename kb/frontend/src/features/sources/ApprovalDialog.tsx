import { ShieldAlert } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { api } from '../../api/api';
import type { ChangePreview, ChangeWarning, SourceChange } from '../../api/types';
import { Badge } from '../../components/ui/Badge';
import { ErrorNotice } from '../../components/ui/ConfirmDialog';
import { Modal } from '../../components/ui/Modal';
import { button } from '../../components/ui/styles';

const WARNING_STYLE: Record<ChangeWarning['level'], string> = {
  info: 'border-slate-700 bg-slate-800/60 text-slate-300',
  warning: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  danger: 'border-rose-500/40 bg-rose-500/10 text-rose-200',
};

export function DiffView({ diff }: { diff: string }) {
  if (!diff) return <p className="text-xs text-slate-500">The file would not change.</p>;
  return (
    <pre className="max-h-72 overflow-auto rounded-xl border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed" aria-label="Changes to the file">
      {diff.split('\n').map((line, index) => {
        const tone = line.startsWith('+++') || line.startsWith('---')
          ? 'text-slate-500'
          : line.startsWith('+')
            ? 'bg-emerald-500/10 text-emerald-300'
            : line.startsWith('-')
              ? 'bg-rose-500/10 text-rose-300'
              : line.startsWith('@@')
                ? 'text-cyan-400'
                : 'text-slate-400';
        return (
          // eslint-disable-next-line react/no-array-index-key
          <div key={index} className={`whitespace-pre-wrap px-1 ${tone}`}>
            {line || ' '}
          </div>
        );
      })}
    </pre>
  );
}

interface Props {
  change: SourceChange;
  preview: ChangePreview;
  /** Extra choices shown with the warnings (e.g. also retire the entries). */
  extra?: ReactNode;
  onApplied: () => void | Promise<void>;
  onClose: () => void;
}

/**
 * The readable warning every change to the source configuration goes through
 * (FR-CFG-10): what the file would look like, what it would do in words, and an
 * explicit "yes" -- nothing is written before it.
 */
export function ApprovalDialog({ change, preview, extra, onApplied, onClose }: Props) {
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasDanger = preview.warnings.some((w) => w.level === 'danger');

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.applySource(change, preview.etag);
      await onApplied();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={preview.summary}
      subtitle="Review before anything is written to config/kb-sources.yaml"
      icon={<ShieldAlert className="h-5 w-5" />}
      width="max-w-3xl"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" className={button.secondary} onClick={onClose} disabled={busy}>
            {preview.changed ? 'Cancel — change nothing' : 'Close'}
          </button>
          {preview.changed ? (
            <button type="button" className={hasDanger ? button.danger : button.primary} onClick={apply} disabled={!understood || busy}>
              {busy ? 'Writing…' : 'Write the change'}
            </button>
          ) : null}
        </>
      }
    >
      <div className="space-y-4 text-xs">
        {!preview.changed ? (
          <p className="rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-slate-300">This would not change the file — it already says that.</p>
        ) : null}

        {preview.warnings.length ? (
          <ul className="space-y-2" aria-label="What this does">
            {preview.warnings.map((warning) => (
              <li key={warning.text} className={`flex gap-2.5 rounded-lg border p-3 leading-relaxed ${WARNING_STYLE[warning.level]}`}>
                <Badge tone={warning.level === 'danger' ? 'rose' : warning.level === 'warning' ? 'amber' : 'slate'}>{warning.level}</Badge>
                <span>{warning.text}</span>
              </li>
            ))}
          </ul>
        ) : null}

        <div>
          <h3 className="mb-1.5 font-semibold text-slate-300">The file, before and after</h3>
          <DiffView diff={preview.diff} />
          <p className="mt-1.5 text-[11px] text-slate-500">
            Only these lines change; comments and <code>${'{VAR}'}</code> placeholders elsewhere are left as written. The previous version is kept in the history.
          </p>
        </div>

        {extra}

        {preview.changed ? (
          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} className="mt-0.5" />
            <span className="text-slate-300">I have read what this does and what changes in the file.</span>
          </label>
        ) : null}

        {error ? <ErrorNotice message={error} /> : null}
      </div>
    </Modal>
  );
}
