import { X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';

interface ModalProps {
  title: string;
  subtitle?: ReactNode;
  icon?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** max-w-* class; the default suits a form. */
  width?: string;
  /** Block closing while something is happening that must not be abandoned. */
  busy?: boolean;
}

export function Modal({ title, subtitle, icon, onClose, children, footer, width = 'max-w-lg', busy }: ModalProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, [onClose, busy]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`flex max-h-[90vh] w-full ${width} flex-col rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl outline-none`}
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 p-5">
          <div className="flex items-start gap-3">
            {icon ? <span className="rounded-xl bg-indigo-500/10 p-2 text-indigo-400">{icon}</span> : null}
            <div>
              <h2 id={titleId} className="text-base font-bold text-white">
                {title}
              </h2>
              {subtitle ? <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p> : null}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="rounded-lg p-1 text-slate-400 hover:text-white disabled:opacity-40"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer ? <div className="flex justify-end gap-2 border-t border-slate-800 p-4">{footer}</div> : null}
      </div>
    </div>
  );
}
