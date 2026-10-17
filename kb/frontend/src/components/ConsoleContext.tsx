import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { api } from '../api/api';
import type { ConfigResponse, Overview } from '../api/types';
import { useResource, type Resource } from '../lib/useResource';

interface ConsoleValue {
  overview: Resource<Overview>;
  config: ConfigResponse | null;
  /** Open the trigger-sync dialog, optionally for one source. */
  triggerSync: { open: boolean; sourceId: string };
  openTrigger: (sourceId?: string) => void;
  closeTrigger: () => void;
}

const Context = createContext<ConsoleValue | null>(null);

export function useConsole(): ConsoleValue {
  const value = useContext(Context);
  if (!value) throw new Error('useConsole must be used inside <ConsoleProvider>');
  return value;
}

export function ConsoleProvider({ children }: { children: ReactNode }) {
  // Fast while something is running, so a sync's progress is visible; slow
  // otherwise, because nothing else changes under a person who is reading.
  const overview = useResource(
    api.overview,
    [],
    (data) => (data && (data.health === 'syncing' || data.queue.queued > 0) ? 4000 : 15000),
  );
  const config = useResource(api.config, []);
  const [trigger, setTrigger] = useState({ open: false, sourceId: '*' });

  const openTrigger = useCallback((sourceId = '*') => setTrigger({ open: true, sourceId }), []);
  const closeTrigger = useCallback(() => setTrigger((t) => ({ ...t, open: false })), []);

  const value = useMemo(
    () => ({ overview, config: config.data, triggerSync: trigger, openTrigger, closeTrigger }),
    [overview, config.data, trigger, openTrigger, closeTrigger],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
