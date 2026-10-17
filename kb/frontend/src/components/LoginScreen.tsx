import { Database, LogIn } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { api } from '../api/api';
import { ApiError } from '../api/client';
import { ErrorNotice } from './ui/ConfirmDialog';
import { button, input, label } from './ui/styles';

export function LoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(token.trim());
      setToken('');
      onLoggedIn();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? 'That is not the admin token.'
          : (err as Error).message,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-2xl border border-slate-800 bg-slate-900 p-7 shadow-2xl">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-600 via-indigo-500 to-cyan-400 text-white">
            <Database className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-white">Knowledge base console</h1>
            <p className="text-xs text-slate-400">Administrators only</p>
          </div>
        </div>

        <div>
          <label htmlFor="token" className={label}>
            Admin token
          </label>
          <input
            id="token"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={token}
            onChange={(e) => setToken(e.target.value)}
            className={input}
            placeholder="KB_ADMIN_TOKEN from .env"
            required
          />
          <p className="mt-1.5 text-[11px] text-slate-500">
            It is the <code className="text-slate-400">KB_ADMIN_TOKEN</code> in the stack&apos;s <code className="text-slate-400">.env</code>.
          </p>
        </div>

        {error ? <ErrorNotice message={error} /> : null}

        <button type="submit" className={`${button.primary} w-full justify-center`} disabled={busy || !token.trim()}>
          <LogIn className="h-4 w-4" />
          {busy ? 'Checking…' : 'Log in'}
        </button>
      </form>
    </main>
  );
}
