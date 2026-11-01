import { cn } from '@/lib/utils';

import { Icon } from './Icon';

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  ariaLabel: string;
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  className,
  ariaLabel,
}: SearchInputProps): JSX.Element {
  return (
    <div className={cn('relative w-full', className)}>
      <Icon
        name="search"
        className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[20px] text-outline"
      />
      <input
        type="search"
        value={value}
        aria-label={ariaLabel}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-11 w-full rounded-xl border border-surface-container-highest bg-surface-container-lowest pl-11 pr-10 text-body-md text-on-surface placeholder:text-outline shadow-sm transition-all focus:border-primary-container focus:outline-none focus:ring-2 focus:ring-accent/20"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-0.5 text-outline transition-colors hover:text-on-surface"
          aria-label="clear"
        >
          <Icon name="close" className="text-[18px]" />
        </button>
      )}
    </div>
  );
}
