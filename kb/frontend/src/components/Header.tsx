import {
  AlertTriangle,
  Calendar,
  Database,
  FileCheck,
  FileText,
  Layers,
  LogOut,
  RefreshCw,
  Server,
} from 'lucide-react';

import { formatDateTime } from '../lib/format';
import { useConsole } from './ConsoleContext';
import { healthStyle } from './ui/Badge';
import { button } from './ui/styles';

export const NAV = [
  { id: 'overview', label: 'Overview', icon: Server },
  { id: 'sources', label: 'Sources', icon: Layers },
  { id: 'forms', label: 'Form records', icon: FileText },
  { id: 'schedules', label: 'Schedules', icon: Calendar },
  { id: 'reports', label: 'Reports', icon: FileCheck },
  { id: 'issues', label: 'Issues', icon: AlertTriangle },
] as const;

interface HeaderProps {
  current: string;
  onNavigate: (name: string) => void;
  onLogout: () => void;
}

export function Header({ current, onNavigate, onLogout }: HeaderProps) {
  const { overview, openTrigger } = useConsole();
  const data = overview.data;
  const style = healthStyle(data?.health);
  const busy = data?.health === 'syncing';

  return (
    <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-16 items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-600 via-indigo-500 to-cyan-400 text-white shadow-lg shadow-indigo-500/20">
              <Database className="h-5 w-5" />
            </div>
            <div>
              <span className="text-lg font-bold tracking-tight text-slate-100">Knowledge base</span>
              <p className="hidden text-xs text-slate-400 sm:block">Sync, sources and manual records</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div
              className="hidden items-center gap-2 rounded-lg border border-slate-800 bg-slate-900 px-3 py-1.5 text-xs md:flex"
              title={data?.problems.join('\n') || undefined}
            >
              <span className={`h-2 w-2 rounded-full ${style.dot}`} aria-hidden="true" />
              <span className={`font-medium ${style.text}`}>{style.label}</span>
              {data?.last_run?.finished_at ? (
                <span className="border-l border-slate-800 pl-2 text-[11px] text-slate-500">
                  Last run {formatDateTime(data.last_run.finished_at)}
                </span>
              ) : null}
            </div>

            <button type="button" className={button.primary} onClick={() => openTrigger()}>
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">Trigger sync</span>
              <span className="sm:hidden">Sync</span>
            </button>
            <button type="button" className={button.icon} onClick={onLogout} title="Log out" aria-label="Log out">
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>

        <nav className="scrollbar-none flex gap-1 overflow-x-auto border-t border-slate-800/80 py-1" aria-label="Sections">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = current === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onNavigate(item.id)}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-2 whitespace-nowrap rounded-lg px-3.5 py-2 text-xs font-medium transition-colors ${
                  active
                    ? 'border border-indigo-500/30 bg-indigo-500/15 text-indigo-400'
                    : 'border border-transparent text-slate-400 hover:bg-slate-900/60 hover:text-slate-200'
                }`}
              >
                <Icon className={`h-4 w-4 ${active ? 'text-indigo-400' : 'text-slate-400'}`} />
                {item.label}
              </button>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
