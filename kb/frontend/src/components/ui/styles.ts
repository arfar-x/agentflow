// Class strings the whole console shares, so a button looks like a button
// everywhere instead of being restyled per screen.

export const button = {
  primary:
    'inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-md shadow-indigo-600/25 transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40',
  secondary:
    'inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3.5 py-2 text-xs font-semibold text-slate-200 transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40',
  danger:
    'inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3.5 py-2 text-xs font-semibold text-white shadow-md shadow-rose-600/25 transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-40',
  warning:
    'inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-3.5 py-2 text-xs font-semibold text-white shadow-md shadow-amber-600/25 transition hover:bg-amber-500 disabled:cursor-not-allowed disabled:opacity-40',
  icon:
    'inline-flex items-center justify-center rounded-lg bg-slate-800 p-1.5 text-slate-300 transition hover:bg-slate-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-40',
  ghost:
    'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-400 transition hover:bg-slate-800 hover:text-slate-200',
};

export const input =
  'w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200 placeholder-slate-600 focus:border-indigo-500 focus:outline-none disabled:opacity-50';

export const label = 'mb-1 block text-xs font-medium text-slate-300';

export const card = 'rounded-xl border border-slate-800 bg-slate-900 shadow-sm';

export const sectionTitle = 'text-sm font-semibold text-slate-200';
