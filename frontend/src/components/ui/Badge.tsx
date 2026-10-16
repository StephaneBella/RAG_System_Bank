import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

import type { Classification, UserRole, UserStatus } from '@/api/types';

type BadgeTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info';

const toneClasses: Record<BadgeTone, string> = {
  neutral: 'bg-surface-container text-on-surface-variant border-outline-variant',
  primary: 'bg-[#eff6ff] text-[#1e40af] border-[#bfdbfe]',
  success: 'bg-secondary-container/40 text-[#005049] border-secondary-container',
  warning: 'bg-[#fffbeb] text-[#b45309] border-[#fcd34d]',
  danger: 'bg-[#fef2f2] text-[#991b1b] border-[#fca5a5]',
  info: 'bg-surface-container-high text-primary border-surface-container-highest',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded border px-1.5 py-px font-badge text-badge font-semibold uppercase tracking-wide',
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const classificationTone: Record<Classification, BadgeTone> = {
  public: 'neutral',
  private: 'primary',
  confidential: 'warning',
};

export function ClassificationBadge({
  classification,
  label,
}: {
  classification?: Classification;
  label: string;
}): JSX.Element {
  return <Badge tone={classificationTone[classification ?? 'public']}>{label}</Badge>;
}

export function RoleBadge({ role, label }: { role: UserRole; label: string }): JSX.Element {
  return <Badge tone={role === 'ADMIN' ? 'info' : 'neutral'}>{label}</Badge>;
}

export function UserStatusBadge({
  status,
  label,
}: {
  status: UserStatus;
  label: string;
}): JSX.Element {
  return <Badge tone={status === 'ACTIVE' ? 'success' : 'neutral'}>{label}</Badge>;
}

export function AuditResultBadge({
  result,
  label,
}: {
  result: 'SUCCESS' | 'FAILURE';
  label: string;
}): JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 font-badge text-badge font-semibold',
        result === 'SUCCESS' ? 'text-secondary' : 'text-error',
      )}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          result === 'SUCCESS' ? 'bg-secondary' : 'bg-error',
        )}
      />
      {label}
    </span>
  );
}
