import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { Icon } from './Icon';

interface PaginationProps {
  page: number;
  pages: number;
  total: number;
  onPageChange: (page: number) => void;
}

export function Pagination({ page, pages, total, onPageChange }: PaginationProps): JSX.Element | null {
  const { t } = useTranslation();
  if (total === 0) return null;

  const canPrev = page > 1;
  const canNext = page < pages;

  return (
    <div className="flex items-center justify-between gap-3 px-1 py-3 text-body-sm text-on-surface-variant">
      <span>
        {t('common.page')} {page} {t('common.of')} {Math.max(pages, 1)} · {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => canPrev && onPageChange(page - 1)}
          disabled={!canPrev}
          aria-label={t('common.previous')}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-lg border border-hairline transition-colors',
            canPrev ? 'hover:bg-surface-container' : 'cursor-not-allowed opacity-40',
          )}
        >
          <Icon name="chevron_left" className="text-[18px]" />
        </button>
        <button
          type="button"
          onClick={() => canNext && onPageChange(page + 1)}
          disabled={!canNext}
          aria-label={t('common.next')}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-lg border border-hairline transition-colors',
            canNext ? 'hover:bg-surface-container' : 'cursor-not-allowed opacity-40',
          )}
        >
          <Icon name="chevron_right" className="text-[18px]" />
        </button>
      </div>
    </div>
  );
}
