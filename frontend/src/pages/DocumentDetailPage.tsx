import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';

import { getErrorStatus } from '@/api/errors';
import { AppShell } from '@/components/layout/AppShell';
import { VisibilityBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Icon } from '@/components/ui/Icon';
import { Skeleton } from '@/components/ui/Skeleton';
import { Spinner } from '@/components/ui/Spinner';
import { useDocument, useDocumentContent } from '@/hooks/useDocuments';
import { useLocale } from '@/hooks/useLocale';
import { downloadBlob, fileExtension, formatDate } from '@/lib/utils';

function useObjectUrl(blob: Blob | undefined): string | undefined {
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    if (!blob) {
      setUrl(undefined);
      return;
    }
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob]);
  return url;
}

export function DocumentDetailPage(): JSX.Element {
  const { t } = useTranslation();
  const locale = useLocale();
  const params = useParams();
  const id = Number(params.id);
  const { data: document, isLoading, isError, error } = useDocument(id);

  const extension = document ? fileExtension(document.file_path) : '';
  const canPreview = extension === 'PDF';
  const contentQuery = useDocumentContent(id, Boolean(document) && canPreview);
  const objectUrl = useObjectUrl(contentQuery.data);

  if (isLoading) {
    return (
      <AppShell>
        <div className="mx-auto w-full max-w-5xl px-6 py-4">
          <Skeleton className="mb-4 h-6 w-64" />
          <Card className="p-6">
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
              <Skeleton className="h-[520px] w-full rounded-xl" />
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

  const fileName = `${document.title}.${extension.toLowerCase() || 'pdf'}`;
  const handleDownload = () => {
    if (contentQuery.data) {
      downloadBlob(contentQuery.data, fileName);
    }
  };

  const meta: { label: string; value: string }[] = [
    { label: t('document.reference'), value: `#${String(document.id).padStart(4, '0')}` },
    { label: t('document.format'), value: extension || 'PDF' },
    { label: t('document.department'), value: document.department_name },
    { label: t('document.createdAt'), value: formatDate(document.created_at, locale) },
    { label: t('document.updatedAt'), value: formatDate(document.updated_at, locale) },
  ];

  return (
    <AppShell>
      <div className="mx-auto w-full max-w-5xl px-6 py-4">
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
          </div>
          {contentQuery.data && (
            <Button variant="secondary" icon="download" onClick={handleDownload}>
              {t('document.download')}
            </Button>
          )}
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
          <Card className="overflow-hidden p-0">
            {canPreview && objectUrl ? (
              <iframe
                src={objectUrl}
                title={document.title}
                className="h-[75vh] w-full border-0 bg-white"
              />
            ) : canPreview && contentQuery.isLoading ? (
              <div className="flex h-[520px] items-center justify-center gap-2 text-on-surface-variant">
                <Spinner />
                <span className="font-body-sm text-body-sm">{t('document.previewLoading')}</span>
              </div>
            ) : canPreview ? (
              <div className="flex h-[520px] flex-col items-center justify-center gap-3 p-8 text-center">
                <Icon name="error" className="text-[40px] text-error" />
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  {t('document.previewError')}
                </p>
              </div>
            ) : (
              <div className="flex h-[520px] flex-col items-center justify-center gap-3 p-8 text-center">
                <Icon name="description" className="text-[44px] text-primary" />
                <p className="font-headline-sm text-headline-sm font-semibold text-on-surface">
                  {document.title}
                </p>
                <p className="max-w-md font-body-sm text-body-sm text-on-surface-variant">
                  {t('document.previewUnavailable')}
                </p>
                <Button variant="primary" icon="download" onClick={handleDownload}>
                  {t('document.download')}
                </Button>
              </div>
            )}
          </Card>

          <aside className="flex flex-col gap-4">
            <div className="flex flex-col gap-3 rounded-xl border border-hairline bg-surface-container-lowest p-4">
              <div className="flex flex-wrap gap-2">
                <VisibilityBadge
                  visibility={document.visibility}
                  label={t(`visibilities.${document.visibility}`)}
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
      </div>
    </AppShell>
  );
}
