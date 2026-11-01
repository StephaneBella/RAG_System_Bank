import { forwardRef, useState, type InputHTMLAttributes, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

import { Icon } from './Icon';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: string;
  leadingIcon?: string;
  /** Rendered at the right of the label row (e.g. "ID-PRO", "Forgot?"). */
  trailingLabel?: ReactNode;
  /** Rendered inside the field, on the right (e.g. password visibility toggle). */
  trailingSlot?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, leadingIcon, trailingLabel, trailingSlot, className, id, ...rest },
  ref,
) {
  return (
    <div className="flex flex-col gap-1.5">
      {(label || trailingLabel) && (
        <div className="flex items-center justify-between gap-2">
          {label && (
            <label htmlFor={id} className="font-label-md text-label-md text-on-surface">
              {label}
            </label>
          )}
          {trailingLabel}
        </div>
      )}
      <div className="relative flex items-center">
        {leadingIcon && (
          <Icon
            name={leadingIcon}
            className="pointer-events-none absolute left-3.5 text-[20px] text-outline"
          />
        )}
        <input
          ref={ref}
          id={id}
          className={cn(
            'h-11 w-full rounded-lg border bg-surface-container-lowest px-3.5 text-body-md text-on-surface placeholder:text-outline transition-all',
            'border-outline-variant focus:border-primary-container focus:outline-none focus:ring-2 focus:ring-accent/20',
            leadingIcon && 'pl-11',
            trailingSlot && 'pr-11',
            error && 'border-error focus:border-error focus:ring-error/20',
            className,
          )}
          aria-invalid={Boolean(error)}
          {...rest}
        />
        {trailingSlot && (
          <div className="absolute right-3 flex items-center text-outline">{trailingSlot}</div>
        )}
      </div>
      {error ? (
        <span className="font-body-sm text-body-sm text-error">{error}</span>
      ) : (
        hint && <span className="font-body-sm text-body-sm text-on-surface-variant">{hint}</span>
      )}
    </div>
  );
});

interface PasswordInputProps extends Omit<InputProps, 'type' | 'trailingSlot'> {
  showLabel: string;
  hideLabel: string;
}

export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  function PasswordInput({ showLabel, hideLabel, ...props }, ref) {
    const [visible, setVisible] = useState(false);
    return (
      <Input
        ref={ref}
        type={visible ? 'text' : 'password'}
        trailingSlot={
          <button
            type="button"
            onClick={() => setVisible((value) => !value)}
            aria-label={visible ? hideLabel : showLabel}
            className="flex items-center rounded p-1 transition-colors hover:text-on-surface"
          >
            <Icon name={visible ? 'visibility_off' : 'visibility'} className="text-[20px]" />
          </button>
        }
        {...props}
      />
    );
  },
);
