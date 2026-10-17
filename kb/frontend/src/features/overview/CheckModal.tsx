import { CheckCircle2, Stethoscope, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api } from '../../api/api';
import type { CheckResponse } from '../../api/types';
import { Modal } from '../../components/ui/Modal';
import { ErrorNotice } from '../../components/ui/ConfirmDialog';
import { button } from '../../components/ui/styles';
import { humanize } from '../../lib/format';

/** `kb check`, from the browser: is each thing the catalog depends on usable. */
export function CheckModal({ onClose }: { onClose: () => void }) {
  const [report, setReport] = useState<CheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .check()
      .then((r) => live && setReport(r))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);

  return (
    <Modal
      title="Dependency check"
      subtitle="The same question `make kb-check` asks"
      icon={<Stethoscope className="h-5 w-5" />}
      onClose={onClose}
      footer={
        <button type="button" className={button.secondary} onClick={onClose}>
          Close
        </button>
      }
    >
      {error ? <ErrorNotice message={error} /> : null}
      {!report && !error ? <p className="py-6 text-center text-xs text-slate-500">Checking — the summarizer check makes a real model call…</p> : null}
      {report ? (
        <ul className="space-y-3">
          {Object.entries(report.checks).map(([name, check]) => (
            <li key={name} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                {check.ok ? <CheckCircle2 className="h-4 w-4 text-emerald-400" /> : <XCircle className="h-4 w-4 text-rose-400" />}
                {humanize(name)}
              </div>
              {check.error ? <p className="mt-1.5 text-xs text-rose-300">{String(check.error)}</p> : null}
              {check.ok && name === 'sources' && Array.isArray(check.enabled) ? (
                <p className="mt-1.5 text-xs text-slate-400">Enabled: {(check.enabled as string[]).join(', ') || 'none'}</p>
              ) : null}
              {check.ok && name === 'summarizer' && check.model ? (
                <p className="mt-1.5 text-xs text-slate-400">Model: {String(check.model)}</p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </Modal>
  );
}
