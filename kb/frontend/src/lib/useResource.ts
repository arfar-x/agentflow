import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';

export interface Resource<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch something now and keep it fresh.
 *
 * Each view asks only for what it shows (the first version of this console
 * refetched everything every 30 s). `intervalMs` may be a function of the data,
 * so a view polls quickly only while something is running and slowly otherwise.
 */
export function useResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  intervalMs?: number | ((data: T | null) => number | null),
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const latest = useRef(fetcher);
  latest.current = fetcher;
  const dataRef = useRef<T | null>(null);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const run = async () => {
      try {
        const next = await latest.current();
        if (cancelled) return;
        dataRef.current = next;
        setData(next);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err as Error);
      } finally {
        if (!cancelled) setLoading(false);
      }
      if (cancelled || intervalMs === undefined) return;
      const wait = typeof intervalMs === 'function' ? intervalMs(dataRef.current) : intervalMs;
      if (wait) timer = setTimeout(run, wait);
    };

    setLoading(true);
    void run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, error, loading, reload };
}
