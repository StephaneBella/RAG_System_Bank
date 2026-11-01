import type { ReactNode } from 'react';

import { Icon } from './Icon';

interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({
  icon = 'search_off',
  title,
  description,
  action,
}: EmptyStateProps): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-container text-primary">
        <Icon name={icon} className="text-[28px]" />
      </div>
      <div>
        <p className="font-headline-sm text-headline-sm font-semibold text-on-surface">{title}</p>
        {description && (
          <p className="mt-1 font-body-sm text-body-sm text-on-surface-variant">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}
