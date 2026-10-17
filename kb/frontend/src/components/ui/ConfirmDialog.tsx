import { AlertTriangle } from 'lucide-react';
import type { ReactNode } from 'react';

import { Modal } from './Modal';
import { button } from './styles';

interface ConfirmDialogProps {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  tone?: 'danger' | 'warning' | 'primary';
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  tone = 'warning',
  busy,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      title={title}
      icon={<AlertTriangle className="h-5 w-5 text-amber-400" />}
      onClose={onCancel}
      busy={busy}
      footer={
        <>
          <button type="button" className={button.secondary} onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={button[tone]} onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="space-y-3 text-xs leading-relaxed text-slate-300">{children}</div>
      {error ? <ErrorNotice message={error} className="mt-4" /> : null}
    </Modal>
  );
}

export function ErrorNotice({ message, className = '' }: { message: string; className?: string }) {
  return (
    <div role="alert" className={`rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-300 ${className}`}>
      {message}
    </div>
  );
}
