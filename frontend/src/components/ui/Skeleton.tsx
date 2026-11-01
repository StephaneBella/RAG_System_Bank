import { cn } from '@/lib/utils';

export function Skeleton({ className }: { className?: string }): JSX.Element {
  return <span className={cn('block animate-pulse rounded bg-surface-container-high', className)} />;
}
