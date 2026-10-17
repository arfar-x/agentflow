import { History } from 'lucide-react';
import { useState } from 'react';

import { api } from '../../api/api';
import { Badge } from '../../components/ui/Badge';
import { ErrorNotice } from '../../components/ui/ConfirmDialog';
import { Modal } from '../../components/ui/Modal';
import { button } from '../../components/ui/styles';
import { formatDateTime } from '../../lib/format';
import { useResource } from '../../lib/useResource';
import { DiffView } from './ApprovalDialog';

const TONE = { create: 'emerald', update: 'indigo', delete: 'rose', settings: 'amber', file: 'cyan' } as const;

/** Every change to the source configuration, from the console or by hand. */
export function RevisionsModal({ onClose }: { onClose: () => void }) {
  const revisions = useResource(() => api.revisions(100), []);
  const [open, setOpen] = useState<number | null>(null);

  return (
    <Modal
      title="Change history"
      subtitle="Changes made here, and changes made to the file by hand"
      icon={<History className="h-5 w-5" />}
      width="max-w-3xl"
      onClose={onClose}
      footer={
        <button type="button" className={button.primary} onClick={onClose}>
          Close
        </button>
      }
    >
      {revisions.error ? <ErrorNotice message={revisions.error.message} /> : null}
      {revisions.data && revisions.data.revisions.length === 0 ? <p className="py-6 text-center text-xs text-slate-500">No changes recorded yet.</p> : null}
      <ul className="divide-y divide-slate-800/80">
        {revisions.data?.revisions.map((revision) => (
          <li key={revision.id} className="py-2.5 text-xs">
            <button type="button" className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left" onClick={() => setOpen(open === revision.id ? null : revision.id)} aria-expanded={open === revision.id}>
              <Badge tone={TONE[revision.action as keyof typeof TONE] ?? 'slate'}>{revision.action === 'file' ? 'edited by hand' : revision.action}</Badge>
              <span className="font-semibold text-slate-200">{revision.source_id ?? 'the whole file'}</span>
              <span className="text-slate-500">{revision.actor}</span>
              <span className="ml-auto text-slate-500">{formatDateTime(revision.at)}</span>
            </button>
            {open === revision.id ? (
              <div className="mt-2">
                <DiffView diff={revision.diff} />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Modal>
  );
}
