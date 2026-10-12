import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import type { User } from '@/api/types';
import { AuditResultBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Modal } from '@/components/ui/Modal';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';
import { useAudit } from '@/hooks/useAudit';
import { useLocale } from '@/hooks/useLocale';
import { formatDateTime } from '@/lib/utils';

interface UserActivityModalProps {
  user: User | null;
  onClose: () => void;
}

export function UserActivityModal({ user, onClose }: UserActivityModalProps): JSX.Element {
  const { t } = useTranslation();
  const locale = useLocale();
  const toast = useToast();
  const auditQuery = useAudit({ page: 1, page_size: 100 }, Boolean(user));

  const events = useMemo(() => {
    const all = auditQuery.data?.items ?? [];
    if (!user) return [];
    return all.filter((log) => log.user_email === user.email).slice(0, 30);
  }, [auditQuery.data, user]);

  if (!user) return <></>;

  return (
    <Modal
      open={Boolean(user)}
      onClose={onClose}
      icon="history"
      size="lg"
      title={t('admin.users.activityTitle')}
      footer={
        <>
          <Button
            variant="ghost"
            icon="download"
            onClick={() => toast.notify(t('admin.users.downloadReport'))}
          >
            {t('admin.users.downloadReport')}
          </Button>
          <Link to="/admin/audit">
            <Button variant="primary" icon="open_in_new">
              {t('admin.users.viewInJournal')}
            </Button>
          </Link>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3 rounded-lg border border-hairline bg-slate-50 p-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-container text-primary">
            <Icon name="person" className="text-[20px]" />
          </span>
          <div className="flex flex-col">
            <span className="font-body-sm text-body-sm font-semibold text-on-surface">
              {user.email}
            </span>
            <span className="font-body-sm text-body-sm text-on-surface-variant">
              {user.department_name}
            </span>
          </div>
        </div>

        <p className="font-body-sm text-body-sm text-on-surface-variant">
          {t('admin.users.activitySubtitle')}
        </p>

        {auditQuery.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-on-surface-variant">
            <Spinner />
            <span className="font-body-sm text-body-sm">{t('common.loading')}</span>
          </div>
        ) : events.length === 0 ? (
          <p className="py-8 text-center font-body-sm text-body-sm text-on-surface-variant">
            {t('admin.audit.empty')}
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-surface-container-high">
            {events.map((event) => (
              <li key={event.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="flex min-w-0 flex-col">
                  <span className="font-body-lg text-body-lg font-medium text-on-surface">
                    {t(`auditActions.${event.action}`)}
                  </span>
                  <span className="truncate font-body-lg text-body-lg text-on-surface-variant">
                    {formatDateTime(event.timestamp, locale)} · {event.resource_label ?? '—'}
                  </span>
                </div>
                <AuditResultBadge
                  result={event.result}
                  label={t(`auditResults.${event.result}`)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
