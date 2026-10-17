import { RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';

import { button } from './styles';
import { ErrorNotice } from './ConfirmDialog';

interface PageProps {
  title: string;
  badge?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  onRefresh?: () => void;
  children: ReactNode;
}

export function Page({ title, badge, description, actions, onRefresh, children }: PageProps) {
  return (
    <section className="space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-white">{title}</h1>
            {badge}
          </div>
          {description ? <p className="mt-1 max-w-3xl text-xs text-slate-400">{description}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          {onRefresh ? (
            <button type="button" className={button.icon} onClick={onRefresh} title="Refresh" aria-label="Refresh">
              <RefreshCw className="h-4 w-4" />
            </button>
          ) : null}
          {actions}
        </div>
      </div>
      {children}
    </section>
  );
}

export function Loading({ what = 'Loading…' }: { what?: string }) {
  return <p className="py-10 text-center text-xs text-slate-500">{what}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-10 text-center text-xs text-slate-400">
      {children}
    </div>
  );
}

export function LoadError({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="space-y-3">
      <ErrorNotice message={error.message} />
      {onRetry ? (
        <button type="button" className={button.secondary} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Notice({ tone = 'amber', children }: { tone?: 'amber' | 'rose' | 'indigo'; children: ReactNode }) {
  const styles = {
    amber: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
    rose: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
    indigo: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-200',
  }[tone];
  return <div className={`rounded-xl border p-4 text-xs leading-relaxed ${styles}`}>{children}</div>;
}
