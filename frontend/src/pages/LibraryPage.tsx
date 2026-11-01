import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import type { DocumentItem, Visibility } from '@/api/types';
import { AppShell } from '@/components/layout/AppShell';
import { VisibilityBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Icon } from '@/components/ui/Icon';
import { Pagination } from '@/components/ui/Pagination';
import { SearchInput } from '@/components/ui/SearchInput';
import { Select } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { useDebounce } from '@/hooks/useDebounce';
import { useDocuments } from '@/hooks/useDocuments';
import { useLocale } from '@/hooks/useLocale';
import { formatDate } from '@/lib/utils';

const VISIBILITIES: Visibility[] = ['public', 'private'];
const PAGE_SIZE = 20;

export function LibraryPage(): JSX.Element {
  const { t } = useTranslation();
  const locale = useLocale();
  const navigate = useNavigate();

  const [search, setSearch] = useState('');
  const [visibility, setVisibility] = useState<Visibility | 'all'>('all');
  const [page, setPage] = useState(1);

  const debouncedSearch = useDebounce(search, 300);

  const documentsQuery = useDocuments({
    page,
    page_size: PAGE_SIZE,
    sort_by: 'updated_at',
    order: 'desc',
  });

  const items = useMemo(() => {
    const all = documentsQuery.data?.items ?? [];
    const needle = debouncedSearch.trim().toLowerCase();
    return all.filter((doc) => {
      if (visibility !== 'all' && doc.visibility !== visibility) return false;
      if (needle && !doc.title.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [documentsQuery.data, debouncedSearch, visibility]);

  const handleSearchChange = (value: string) => {
    setSearch(value);
    setPage(1);
  };

  const openDocument = (doc: DocumentItem) => {
    navigate(`/documents/${doc.id}`);
  };

  return (
    <AppShell>
      <div className="mx-auto w-full max-w-4xl px-6 py-6">
        <div className="flex flex-col gap-4 pt-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="font-headline-lg text-headline-lg font-semibold text-on-surface">
              {t('library.title')}
            </h1>
            <p className="mt-0.5 font-body-md text-body-md text-on-surface-variant">
              {t('library.subtitle')}
            </p>
          </div>
          <div className="shrink-0 rounded-lg bg-surface-container-low px-3 py-1.5 font-body-sm text-body-sm text-on-surface-variant">
            {t('common.documentsAvailable', { count: documentsQuery.data?.total ?? 0 })}
          </div>
        </div>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row">
          <SearchInput
            value={search}
            onChange={handleSearchChange}
            placeholder={t('library.searchPlaceholder')}
            ariaLabel={t('common.search')}
          />
          <Select
            value={visibility}
            onChange={(event) => setVisibility(event.target.value as Visibility | 'all')}
            className="sm:w-56"
            aria-label={t('library.visibilityLabel')}
          >
            <option value="all">{t('library.allVisibilities')}</option>
            {VISIBILITIES.map((value) => (
              <option key={value} value={value}>
                {t(`visibilities.${value}`)}
              </option>
            ))}
          </Select>
        </div>

        <Card className="mt-4 overflow-hidden">
          {documentsQuery.isLoading ? (
            <ul className="flex flex-col divide-y divide-surface-container-high">
              {Array.from({ length: 5 }).map((_, index) => (
                <li key={index} className="flex items-center justify-between gap-4 p-4">
                  <div className="flex min-w-0 flex-1 items-center gap-3.5">
                    <Skeleton className="h-10 w-10 rounded-lg" />
                    <div className="flex flex-col gap-2">
                      <Skeleton className="h-3.5 w-48" />
                      <Skeleton className="h-3 w-24" />
                    </div>
                  </div>
                  <Skeleton className="h-5 w-20 rounded" />
                </li>
              ))}
            </ul>
          ) : documentsQuery.isError ? (
            <EmptyState
              icon="wifi_off"
              title={t('errors.generic')}
              description={undefined}
              action={
                <Button
                  variant="secondary"
                  icon="refresh"
                  onClick={() => void documentsQuery.refetch()}
                >
                  {t('common.retry')}
                </Button>
              }
            />
          ) : items.length === 0 ? (
            <EmptyState
              icon="search_off"
              title={t('library.noResultsTitle')}
              description={t('library.noResultsHint')}
              action={
                <Button
                  variant="secondary"
                  icon="restart_alt"
                  onClick={() => {
                    setSearch('');
                    setVisibility('all');
                  }}
                >
                  {t('library.resetFilters')}
                </Button>
              }
            />
          ) : (
            <ul className="flex flex-col divide-y divide-surface-container-high">
              {items.map((doc) => (
                <li key={doc.id}>
                  <button
                    type="button"
                    onClick={() => openDocument(doc)}
                    className="flex w-full items-center justify-between gap-4 p-4 text-left transition-colors hover:bg-surface-container-low/60"
                  >
                    <div className="flex min-w-0 flex-1 items-center gap-3.5">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface-container text-primary">
                        <Icon name="description" className="text-[20px]" />
                      </span>
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate font-body-lg text-body-lg font-semibold text-on-surface">
                          {doc.title}
                        </span>
                        <span className="mt-0.5 font-body-lg text-body-lg text-on-surface-variant">
                          {formatDate(doc.updated_at ?? doc.created_at, locale)}
                        </span>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <VisibilityBadge
                        visibility={doc.visibility}
                        label={t(`visibilities.${doc.visibility}`)}
                      />
                      <Icon name="chevron_right" className="text-[20px] text-outline" />
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {documentsQuery.data && (
          <Pagination
            page={documentsQuery.data.page}
            pages={documentsQuery.data.pages}
            total={documentsQuery.data.total}
            onPageChange={setPage}
          />
        )}
      </div>
    </AppShell>
  );
}
