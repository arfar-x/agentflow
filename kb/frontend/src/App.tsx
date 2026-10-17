import { useCallback, useEffect, useState } from 'react';

import { api } from './api/api';
import { setUnauthorizedHandler } from './api/client';
import { ConsoleProvider, useConsole } from './components/ConsoleContext';
import { Header, NAV } from './components/Header';
import { LoginScreen } from './components/LoginScreen';
import { Loading } from './components/ui/Page';
import { ToastProvider } from './components/ui/Toast';
import { FormsView } from './features/forms/FormsView';
import { IssuesView } from './features/issues/IssuesView';
import { OverviewView } from './features/overview/OverviewView';
import { RunsView } from './features/runs/RunsView';
import { SchedulesView } from './features/schedules/SchedulesView';
import { SourcesView } from './features/sources/SourcesView';
import { TriggerSyncModal } from './features/sync/TriggerSyncModal';
import { useRoute } from './lib/route';

type Auth = 'checking' | 'out' | 'in';

export default function App() {
  const [auth, setAuth] = useState<Auth>('checking');

  useEffect(() => {
    setUnauthorizedHandler(() => setAuth('out'));
    api
      .session()
      .then((s) => setAuth(s.authenticated ? 'in' : 'out'))
      .catch(() => setAuth('out'));
    return () => setUnauthorizedHandler(null);
  }, []);

  if (auth === 'checking') return <Loading />;
  if (auth === 'out') return <LoginScreen onLoggedIn={() => setAuth('in')} />;
  return (
    <ToastProvider>
      <ConsoleProvider>
        <Shell onLogout={() => api.logout().finally(() => setAuth('out'))} />
      </ConsoleProvider>
    </ToastProvider>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const [route, navigate] = useRoute();
  const { triggerSync, closeTrigger } = useConsole();
  const known = NAV.some((n) => n.id === route.name);
  const current = known ? route.name : 'overview';
  const runId = route.name === 'reports' && route.param && /^\d+$/.test(route.param) ? Number(route.param) : null;

  const openRun = useCallback((id: number) => navigate('reports', id), [navigate]);

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 font-sans text-slate-100 selection:bg-indigo-500/30 selection:text-white">
      <Header current={current} onNavigate={(name) => navigate(name)} onLogout={onLogout} />
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">
        {current === 'overview' && <OverviewView onNavigate={navigate} />}
        {current === 'sources' && <SourcesView onNavigate={navigate} focusId={route.param} />}
        {current === 'forms' && <FormsView param={route.param} onNavigate={navigate} />}
        {current === 'schedules' && <SchedulesView onNavigate={navigate} />}
        {current === 'reports' && <RunsView openRunId={runId} onOpenRun={openRun} onCloseRun={() => navigate('reports')} />}
        {current === 'issues' && <IssuesView onOpenRun={openRun} onWriteRecord={(query) => navigate('forms', `new:${query}`)} />}
      </main>
      {triggerSync.open ? (
        <TriggerSyncModal initialSourceId={triggerSync.sourceId} onClose={closeTrigger} onOpenRun={openRun} />
      ) : null}
      <footer className="border-t border-slate-900 py-4 text-center text-[11px] text-slate-500">
        Changes here reach the catalog through the scheduler; this console never writes it directly.
      </footer>
    </div>
  );
}
