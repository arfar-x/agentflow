interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  tone?: 'indigo' | 'purple' | 'emerald';
  disabled?: boolean;
}

export function Toggle({ checked, onChange, label, tone = 'indigo', disabled }: ToggleProps) {
  const on = { indigo: 'bg-indigo-600', purple: 'bg-purple-600', emerald: 'bg-emerald-600' }[tone];
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${checked ? on : 'bg-slate-700'}`}
    >
      <span className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-[22px]' : 'translate-x-0.5'}`} />
    </button>
  );
}
