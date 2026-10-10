import { forwardRef, type ReactNode, type TextareaHTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: ReactNode;
  error?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, error, className, id, ...rest },
  ref,
) {
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={id} className="font-label-md text-label-md text-on-surface">
          {label}
        </label>
      )}
      <textarea
        ref={ref}
        id={id}
        className={cn(
          'min-h-[88px] w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-3.5 py-2.5 text-body-md text-on-surface placeholder:text-outline transition-all',
          'focus:border-primary-container focus:outline-none focus:ring-2 focus:ring-accent/20',
          error && 'border-error',
          className,
        )}
        {...rest}
      />
      {error && <span className="font-body-sm text-body-sm text-error">{error}</span>}
    </div>
  );
});
