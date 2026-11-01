import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/utils';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  padded?: boolean;
}

export function Card({ children, padded = false, className, ...rest }: CardProps): JSX.Element {
  return (
    <div
      className={cn(
        'rounded-xl border border-hairline bg-surface-container-lowest shadow-card',
        padded && 'p-5',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}
