import { forwardRef, type ReactNode, type SelectHTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

import { Icon } from './Icon';

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: ReactNode;
  error?: string;
  hint?: ReactNode;
  options?: { value: string | number; label: string }[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, error, hint, options, className, id, children, ...rest },
  ref,
) {
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={id} className="font-label-md text-label-md text-on-surface">
          {label}
        </label>
      )}
      <div className="relative">
        <select
          ref={ref}
          id={id}
          className={cn(
            'h-11 w-full appearance-none rounded-lg border border-outline-variant bg-surface-container-lowest pl-3.5 pr-10 text-body-md text-on-surface transition-all',
            'focus:border-primary-container focus:outline-none focus:ring-2 focus:ring-accent/20',
            error && 'border-error',
            className,
          )}
          aria-invalid={Boolean(error)}
          {...rest}
        >
          {children}
          {options?.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <Icon
          name="expand_more"
          className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[18px] text-outline"
        />
      </div>
      {error ? (
        <span className="font-body-sm text-body-sm text-error">{error}</span>
      ) : (
        hint && <span className="font-body-sm text-body-sm text-on-surface-variant">{hint}</span>
      )}
    </div>
  );
});
