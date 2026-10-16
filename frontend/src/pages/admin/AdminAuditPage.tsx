import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { getApiErrorMessage } from '@/api/errors';
import { auditApi } from '@/api';
import type { AuditAction, AuditResult } from '@/api/types';
import { AuditResultBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Icon } from '@/components/ui/Icon';
import { Pagination } from '@/components/ui/Pagination';
import { SearchInput } from '@/components/ui/SearchInput';
import { SegmentedControl } from '@/components/ui/SegmentedControl';
import { Select } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { useAudit } from '@/hooks/useAudit';
import { useDebounce } from '@/hooks/useDebounce';
import { useLocale } from '@/hooks/useLocale';
import { downloadBlob, formatDateTime } from '@/lib/utils';

type Period = 'all' | 'today' | 'last7' | 'last30' | 'month';

const PAGE_SIZE = 20;
const ACTIONS: AuditAction[] = [
  'LOGIN',
  'LOGOUT',
  'DOCUMENT_VIEW',
  'DOCUMENT_SEARCH',
  'DOCUMENT_CREATE',
  'DOCUMENT_UPDATE',
  'DOCUMENT_DELETE',
  'USER_CREATE',
  'USER_UPDATE',
  'USER_STATUS_UPDATE',
  'EXPORT',
];
const RESULTS: AuditResult[] = ['SUCCESS', 'FAILURE'];

const PERIODS: { value: Period; labelKey: string }[] = [
  { value: 'all', labelKey: 'allDates' },
  { value: 'today', labelKey: 'today' },
  { value: 'last7', labelKey: 'last7' },
  { value: 'last30', labelKey: 'last30' },
  { value: 'month', labelKey: 'thisMonth' },
];

export function AdminAuditPage(): JSX.Element {
  const { t } = useTranslation();
  const locale = useLocale();
  const toast = useToast();

  const [page, setPage] = useState(1);
  const [period, setPeriod] = useState<Period>('all');
  const [action, setAction] = useState<AuditAction | 'ALL'>('ALL');
  const [result, setResult] = useState<AuditResult | 'ALL'>('ALL');
  const [userEmail, setUserEmail] = useState('');
  const [exporting, setExporting] = useState(false);

  const debouncedUserEmail = useDebounce(userEmail, 300);
  const userEmailFilter = debouncedUserEmail.trim() || undefined;

  const queryParams = {
    page,
    page_size: PAGE_SIZE,
    period,
    action,
    result,
    user_email: userEmailFilter,
  };
  const auditQuery = useAudit(queryParams);
  const rows = auditQuery.data?.items ?? [];

  const handleExport = async () => {
    setExporting(true);
    try {
      const blob = await auditApi.exportAudit({ period, action, result, user_email: userEmailFilter });
      downloadBlob(blob, 'audit-log.csv');
      toast.success(t('admin.audit.exportDone'));
    } catch (error) {
      toast.error(getApiErrorMessage(error) ?? t('errors.generic'));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-headline-lg text-headline-lg font-semibold text-on-surface">
            {t('admin.audit.title')}
          </h1>
          <p className="mt-0.5 font-body-md text-body-md text-on-surface-variant">
            {t('admin.audit.subtitle')}
          </p>
        </div>
        <Button
          variant="secondary"
          icon="download"
          isLoading={exporting}
          onClick={() => void handleExport()}
        >
          {exporting ? t('admin.audit.exporting') : t('admin.audit.export')}
        </Button>
      </div>

      <div className="flex flex-col gap-3">
        <div className="overflow-x-auto">
          <SegmentedControl<Period>
            value={period}
            onChange={(value) => {
              setPeriod(value);
              setPage(1);
            }}
            options={PERIODS.map((item) => ({
              value: item.value,
              label: t(`admin.audit.${item.labelKey}`),
            }))}
          />
        </div>
        <div className="flex flex-col gap-3 sm:flex-row">
          <SearchInput
            value={userEmail}
            onChange={(value) => {
              setUserEmail(value);
              setPage(1);
            }}
            placeholder={t('admin.audit.searchUserPlaceholder')}
            ariaLabel={t('admin.audit.columns.user')}
            className="sm:flex-1"
          />
          <Select
            value={action}
            onChange={(event) => {
              setAction(event.target.value as AuditAction | 'ALL');
              setPage(1);
            }}
            className="sm:w-56"
            aria-label={t('admin.audit.columns.event')}
          >
            <option value="ALL">{t('admin.audit.allEvents')}</option>
            {ACTIONS.map((value) => (
              <option key={value} value={value}>
                {t(`auditActions.${value}`)}
              </option>
            ))}
          </Select>
          <Select
            value={result}
            onChange={(event) => {
              setResult(event.target.value as AuditResult | 'ALL');
              setPage(1);
            }}
            className="sm:w-44"
            aria-label={t('admin.audit.columns.status')}
          >
            <option value="ALL">{t('admin.audit.allStatuses')}</option>
            {RESULTS.map((value) => (
              <option key={value} value={value}>
                {t(`auditResults.${value}`)}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <Card className="overflow-hidden">
        {auditQuery.isLoading ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <tbody className="divide-y divide-surface-container-high">
                {Array.from({ length: 6 }).map((_, index) => (
                  <tr key={index}>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-32" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-40" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-28" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-36" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-40" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-16" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon="history_toggle_off" title={t('admin.audit.empty')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-hairline bg-surface-container-low">
                  <th className="whitespace-nowrap px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.audit.columns.dateTime')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.audit.columns.user')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.audit.columns.event')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.audit.columns.resource')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.audit.columns.context')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.audit.columns.status')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-high">
                {rows.map((log) => (
                  <tr key={log.id} className="transition-colors hover:bg-surface-container-low/50">
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-body-table text-on-surface-variant tabular">
                      {formatDateTime(log.timestamp, locale)}
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-body-table text-body-table font-medium text-on-surface">
                        {log.user_email}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-1.5 font-body-table text-body-table font-medium text-on-surface">
                        <Icon name="bolt" className="text-[16px] text-primary" />
                        {t(`auditActions.${log.action}`)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-body-table text-body-table text-on-surface-variant">
                        {t(`resourceTypes.${log.resource_type}`, {
                          defaultValue: log.resource_type,
                        })}{' '}
                        {log.resource_id !== null ? `#${log.resource_id}` : '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-body-table text-on-surface-variant tabular">
                        {log.ip_address ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <AuditResultBadge
                        result={log.result}
                        label={t(`auditResults.${log.result}`)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <span className="font-body-sm text-body-sm text-on-surface-variant">
        {t('admin.audit.foundCount', { count: auditQuery.data?.total ?? 0 })}
      </span>

      {auditQuery.data && (
        <Pagination
          page={auditQuery.data.page}
          pages={auditQuery.data.pages}
          total={auditQuery.data.total}
          onPageChange={setPage}
        />
      )}
    </div>
  );
}
