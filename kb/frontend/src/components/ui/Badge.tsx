import type { ReactNode } from 'react';

import type { Health, RunStatus, Trigger } from '../../api/types';

type Tone = 'slate' | 'indigo' | 'emerald' | 'amber' | 'rose' | 'cyan' | 'purple' | 'blue';

const TONES: Record<Tone, string> = {
  slate: 'border-slate-700 bg-slate-800 text-slate-300',
  indigo: 'border-indigo-500/30 bg-indigo-500/15 text-indigo-300',
  emerald: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
  amber: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  rose: 'border-rose-500/30 bg-rose-500/15 text-rose-300',
  cyan: 'border-cyan-500/30 bg-cyan-500/15 text-cyan-300',
  purple: 'border-purple-500/30 bg-purple-500/15 text-purple-300',
  blue: 'border-blue-500/30 bg-blue-500/15 text-blue-300',
};

export function Badge({ tone = 'slate', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

const STATUS_TONE: Record<RunStatus, Tone> = {
  running: 'blue',
  success: 'emerald',
  warning: 'amber',
  error: 'rose',
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{status}</Badge>;
}

const TRIGGER_TONE: Record<Trigger, Tone> = {
  scheduled: 'slate',
  manual: 'indigo',
  webhook: 'cyan',
  cli: 'purple',
};

export function TriggerBadge({ trigger }: { trigger: Trigger }) {
  return <Badge tone={TRIGGER_TONE[trigger]}>{trigger}</Badge>;
}

const HEALTH: Record<Health, { dot: string; text: string; label: string }> = {
  healthy: { dot: 'bg-emerald-500', text: 'text-emerald-400', label: 'Healthy' },
  syncing: { dot: 'bg-blue-500 animate-pulse', text: 'text-blue-400', label: 'Sync in progress' },
  degraded: { dot: 'bg-amber-500', text: 'text-amber-400', label: 'Degraded' },
  error: { dot: 'bg-rose-500', text: 'text-rose-400', label: 'Needs attention' },
};

export function healthStyle(health: Health | undefined) {
  return health ? HEALTH[health] : { dot: 'bg-slate-500', text: 'text-slate-400', label: 'Connecting' };
}

export function StatusDot({ tone }: { tone: 'ok' | 'warn' | 'bad' | 'busy' | 'idle' }) {
  const color = {
    ok: 'bg-emerald-400',
    warn: 'bg-amber-400',
    bad: 'bg-rose-400',
    busy: 'bg-blue-400 animate-pulse',
    idle: 'bg-slate-500',
  }[tone];
  return <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${color}`} />;
}

export function runTone(status: RunStatus | undefined): 'ok' | 'warn' | 'bad' | 'busy' | 'idle' {
  switch (status) {
    case 'success':
      return 'ok';
    case 'warning':
      return 'warn';
    case 'error':
      return 'bad';
    case 'running':
      return 'busy';
    default:
      return 'idle';
  }
}
