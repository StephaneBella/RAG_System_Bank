import { cn } from '@/lib/utils';

export function Spinner({ className }: { className?: string }): JSX.Element {
  return (
    <span
      role="status"
      aria-label="loading"
      className={cn(
        'inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent',
        className,
      )}
    />
  );
}
