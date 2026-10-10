import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';

import { getErrorStatus } from '@/api/errors';
import { AppShell } from '@/components/layout/AppShell';
import { ClassificationBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Icon } from '@/components/ui/Icon';
import { Skeleton } from '@/components/ui/Skeleton';
import { useDocument } from '@/hooks/useDocuments';
import { useLocale } from '@/hooks/useLocale';
import { fileExtension, formatDate, formatFileSize } from '@/lib/utils';

export function DocumentDetailPage(): JSX.Element {
  const { t } = useTranslation();
  const locale = useLocale();
  const params = useParams();
  const id = Number(params.id);
  const { data: document, isLoading, isError, error } = useDocument(id);

  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(100);

  if (isLoading) {
    return (
      <AppShell>
        <div className="mx-auto w-full max-w-4xl px-6 py-4">
          <Skeleton className="mb-4 h-6 w-64" />
          <Card className="p-6">
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
              <Skeleton className="h-[380px] w-full rounded-xl" />
              <div className="flex flex-col gap-3">
                <Skeleton className="h-32 w-full rounded-xl" />
                <Skeleton className="h-48 w-full rounded-xl" />
              </div>
            </div>
          </Card>
        </div>
      </AppShell>
    );
  }

  if (isError || !document) {
    const status = getErrorStatus(error);
    const forbidden = status === 403;
    return (
      <AppShell>
        <div className="mx-auto max-w-2xl px-6 py-16">
          <EmptyState
            icon={forbidden ? 'lock' : 'description'}
            title={forbidden ? t('document.forbiddenTitle') : t('document.notFoundTitle')}
            description={forbidden ? t('document.forbiddenBody') : t('document.notFoundBody')}
            action={
              <Link to="/library">
                <Button variant="secondary" icon="arrow_back">
                  {t('nav.backToLibrary')}
                </Button>
              </Link>
            }
          />
        </div>
      </AppShell>
    );
  }

  const meta: { label: string; value: string }[] = [
    { label: t('document.reference'), value: `#${String(document.id).padStart(4, '0')}` },
    { label: t('document.format'), value: fileExtension(document.file_name) || 'PDF' },
    { label: t('document.size'), value: formatFileSize(document.file_size) },
    { label: t('document.department'), value: document.department_name },
    { label: t('document.createdAt'), value: formatDate(document.created_at, locale) },
    { label: t('document.updatedAt'), value: formatDate(document.updated_at, locale) },
  ];

  return (
    <AppShell>
      <div className="mx-auto w-full max-w-4xl px-6 py-4">
        <div className="flex items-start justify-between gap-4 pb-4">
          <div className="flex min-w-0 flex-col">
            <Link
              to="/library"
              className="mb-1 inline-flex items-center gap-1 font-body-sm text-body-sm text-primary hover:underline"
            >
              <Icon name="arrow_back" className="text-[16px]" />
              {t('nav.backToLibrary')}
            </Link>
            <h1 className="truncate font-headline-md text-headline-md font-bold text-primary">
              {document.title}
            </h1>
            {document.description && (
              <p className="font-body-sm text-body-sm text-on-surface-variant">
                {document.description}
              </p>
            )}
          </div>
        </div>

        <Card className="overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-surface-container bg-surface-container-low px-4 py-2.5">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPage((value) => Math.max(1, value - 1))}
                className="flex h-8 w-8 items-center justify-center rounded bg-surface-container-lowest text-on-surface-variant transition-colors hover:bg-surface-container"
                title={t('common.previous')}
              >
                <Icon name="chevron_left" className="text-[18px]" />
              </button>
              <span className="px-2 font-label-sm text-label-sm font-medium text-on-surface">
                {t('document.page')} {page}
              </span>
              <button
                type="button"
                onClick={() => setPage((value) => value + 1)}
                className="flex h-8 w-8 items-center justify-center rounded bg-surface-container-lowest text-on-surface-variant transition-colors hover:bg-surface-container"
                title={t('common.next')}
              >
                <Icon name="chevron_right" className="text-[18px]" />
              </button>
            </div>
            <div className="flex items-center gap-1 rounded bg-surface-container-lowest px-1">
              <button
                type="button"
                onClick={() => setZoom((value) => Math.max(50, value - 10))}
                title={t('document.zoomOut')}
                className="flex h-7 w-7 items-center justify-center text-on-surface-variant hover:text-primary"
              >
                <Icon name="zoom_out" className="text-[18px]" />
              </button>
              <span className="px-2 font-mono text-code-mono font-semibold text-on-surface tabular">
                {zoom}%
              </span>
              <button
                type="button"
                onClick={() => setZoom((value) => Math.min(200, value + 10))}
                title={t('document.zoomIn')}
                className="flex h-7 w-7 items-center justify-center text-on-surface-variant hover:text-primary"
              >
                <Icon name="zoom_in" className="text-[18px]" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 p-6 lg:grid-cols-[1fr_320px]">
            <div className="flex min-h-[380px] flex-col items-center justify-center gap-3 rounded-xl border border-surface-container-high bg-surface-container-low p-8 text-center">
              <Icon name="menu_book" className="text-[44px] text-primary" />
              <p className="font-headline-sm text-headline-sm font-semibold text-on-surface">
                {t('document.viewer')}
              </p>
            </div>

            <aside className="flex flex-col gap-4">
              <div className="flex flex-col gap-3 rounded-xl border border-hairline bg-surface-container-lowest p-4">
                <div className="flex flex-wrap gap-2">
                  <ClassificationBadge
                    classification={document.classification}
                    label={t(`classifications.${document.classification ?? 'private'}`)}
                  />
                </div>
                <dl className="flex flex-col divide-y divide-surface-container-high">
                  {meta.map((item) => (
                    <div key={item.label} className="flex items-center justify-between gap-3 py-2">
                      <dt className="font-label-sm text-label-sm uppercase tracking-wider text-faint">
                        {item.label}
                      </dt>
                      <dd className="truncate font-body-sm text-body-sm text-on-surface">
                        {item.value}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            </aside>
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
