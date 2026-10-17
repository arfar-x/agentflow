import { useCallback, useEffect, useState } from 'react';

// A hash route (#/reports/12) rather than a router library: six screens and one
// optional id do not justify a dependency, and a hash needs nothing from the
// server to survive a reload.

export interface Route {
  name: string;
  param: string | null;
}

export function parseHash(hash: string): Route {
  const [name, param] = hash.replace(/^#\/?/, '').split('/');
  return { name: name || 'overview', param: param ? decodeURIComponent(param) : null };
}

export function useRoute(): [Route, (name: string, param?: string | number | null) => void] {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const navigate = useCallback((name: string, param?: string | number | null) => {
    window.location.hash = param === undefined || param === null ? `#/${name}` : `#/${name}/${encodeURIComponent(String(param))}`;
  }, []);

  return [route, navigate];
}
