import { cn } from '@/lib/utils';

interface SegmentedControlProps<T extends string> {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  className?: string;
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  className,
}: SegmentedControlProps<T>): JSX.Element {
  return (
    <div
      className={cn(
        'inline-flex items-center rounded-lg border border-hairline bg-slate-100 p-0.5',
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={cn(
              'rounded-md px-3 py-1.5 font-label-md text-label-md transition-colors',
              active
                ? 'bg-surface-container-lowest font-semibold text-ink shadow-[0_1px_2px_rgba(0,0,0,0.05)]'
                : 'text-muted hover:bg-hairline',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
