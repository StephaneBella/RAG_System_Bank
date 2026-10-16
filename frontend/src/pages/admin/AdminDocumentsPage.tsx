import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import { getApiErrorMessage } from '@/api/errors';
import type { DocumentItem } from '@/api/types';
import type { DocumentFormValues } from '@/api/documents.api';
import { DocumentFormModal } from '@/components/documents/DocumentFormModal';
import { VisibilityBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Icon } from '@/components/ui/Icon';
import { Modal } from '@/components/ui/Modal';
import { Pagination } from '@/components/ui/Pagination';
import { SearchInput } from '@/components/ui/SearchInput';
import { Select } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { useCreateDocument, useDeleteDocument, useDocuments, useUpdateDocument } from '@/hooks/useDocuments';
import { useDepartments } from '@/hooks/useDepartments';
import { useLocale } from '@/hooks/useLocale';
import { formatDate } from '@/lib/utils';

const PAGE_SIZE = 10;

type EditorState = { mode: 'create' } | { mode: 'edit'; document: DocumentItem } | null;

export function AdminDocumentsPage(): JSX.Element {
  const { t } = useTranslation();
  const locale = useLocale();
  const toast = useToast();
  const navigate = useNavigate();

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [departmentId, setDepartmentId] = useState('');
  const [editor, setEditor] = useState<EditorState>(null);
  const [toDelete, setToDelete] = useState<DocumentItem | null>(null);
  const [serverError, setServerError] = useState<string | undefined>();

  const documentsQuery = useDocuments({
    page,
    page_size: PAGE_SIZE,
    sort_by: 'created_at',
    order: 'desc',
  });
  const departmentsQuery = useDepartments();
  const createMutation = useCreateDocument();
  const updateMutation = useUpdateDocument();
  const deleteMutation = useDeleteDocument();

  const departments = departmentsQuery.data ?? [];
  const rows = useMemo(() => {
    const all = documentsQuery.data?.items ?? [];
    const needle = search.trim().toLowerCase();
    return all.filter((doc) => {
      if (departmentId && String(doc.department_id) !== departmentId) return false;
      if (needle && !doc.title.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [documentsQuery.data, search, departmentId]);

  const clearError = () => setServerError(undefined);

  const handleSubmit = async (values: DocumentFormValues) => {
    clearError();
    try {
      if (editor?.mode === 'edit') {
        await updateMutation.mutateAsync({ id: editor.document.id, values });
        toast.success(t('admin.documents.updatedToast'));
      } else {
        await createMutation.mutateAsync(values);
        toast.success(t('admin.documents.createdToast'));
      }
      setEditor(null);
    } catch (error) {
      setServerError(getApiErrorMessage(error) ?? t('errors.generic'));
    }
  };

  const handleDelete = async () => {
    if (!toDelete) return;
    try {
      await deleteMutation.mutateAsync(toDelete.id);
      toast.success(t('admin.documents.deletedToast'));
      setToDelete(null);
    } catch (error) {
      toast.error(getApiErrorMessage(error) ?? t('errors.generic'));
    }
  };

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-headline-lg text-headline-lg font-semibold text-on-surface">
            {t('admin.documents.title')}
          </h1>
          <p className="mt-0.5 font-body-md text-body-md text-on-surface-variant">
            {t('admin.documents.subtitle')}
          </p>
        </div>
        <Button
          variant="primary"
          icon="add"
          onClick={() => {
            clearError();
            setEditor({ mode: 'create' });
          }}
        >
          {t('admin.documents.add')}
        </Button>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        <SearchInput
          value={search}
          onChange={(value) => setSearch(value)}
          placeholder={t('admin.documents.searchPlaceholder')}
          ariaLabel={t('common.search')}
        />
        <Select
          value={departmentId}
          onChange={(event) => setDepartmentId(event.target.value)}
          className="sm:w-64"
          aria-label={t('admin.documents.departmentFilter')}
        >
          <option value="">{t('admin.documents.allDepartments')}</option>
          {departments.map((department) => (
            <option key={department.id} value={department.id}>
              {t(`departments.${department.name}`, { defaultValue: department.name })}
            </option>
          ))}
        </Select>
      </div>

      <Card className="overflow-hidden">
        {documentsQuery.isLoading ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <tbody className="divide-y divide-surface-container-high">
                {Array.from({ length: 6 }).map((_, index) => (
                  <tr key={index}>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <Skeleton className="h-8 w-8 rounded-lg" />
                        <Skeleton className="h-3.5 w-40" />
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-28" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-24" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-5 w-20 rounded" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="ml-auto h-3.5 w-24" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon="folder_off"
            title={t('common.noResults')}
            action={
              <Button
                variant="secondary"
                icon="restart_alt"
                onClick={() => {
                  setSearch('');
                  setDepartmentId('');
                }}
              >
                {t('library.resetFilters')}
              </Button>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-hairline bg-surface-container-low">
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.documents.columns.title')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.documents.columns.department')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.documents.columns.date')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.documents.columns.status')}
                  </th>
                  <th className="px-4 py-3 text-right font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.documents.columns.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-high">
                {rows.map((doc) => (
                  <tr key={doc.id} className="transition-colors hover:bg-surface-container-low/50">
                    <td className="max-w-xs px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-container text-primary">
                          <Icon name="description" className="text-[18px]" />
                        </span>
                        <span className="truncate font-body-table text-body-table font-medium text-on-surface">
                          {doc.title}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-body-table text-body-table text-on-surface-variant">
                      {doc.department_name}
                    </td>
                    <td className="px-4 py-3 font-body-table text-body-table text-on-surface-variant">
                      {formatDate(doc.updated_at ?? doc.created_at, locale)}
                    </td>
                    <td className="px-4 py-3">
                      <VisibilityBadge
                        visibility={doc.visibility}
                        label={t(`visibilities.${doc.visibility}`)}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => navigate(`/documents/${doc.id}`)}
                          title={t('common.view')}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-primary"
                        >
                          <Icon name="visibility" className="text-[18px]" />
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            clearError();
                            setEditor({ mode: 'edit', document: doc });
                          }}
                          title={t('common.edit')}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-primary"
                        >
                          <Icon name="edit" className="text-[18px]" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setToDelete(doc)}
                          title={t('common.delete')}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-error-container hover:text-error"
                        >
                          <Icon name="delete" className="text-[18px]" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="flex items-center justify-between gap-3">
        <span className="font-body-sm text-body-sm text-on-surface-variant">
          {t('admin.documents.foundCount', { count: documentsQuery.data?.total ?? 0 })}
        </span>
      </div>

      {documentsQuery.data && (
        <Pagination
          page={documentsQuery.data.page}
          pages={documentsQuery.data.pages}
          total={documentsQuery.data.total}
          onPageChange={setPage}
        />
      )}

      <DocumentFormModal
        open={editor !== null}
        onClose={() => {
          clearError();
          setEditor(null);
        }}
        document={editor?.mode === 'edit' ? editor.document : null}
        departments={departments}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
        serverError={serverError}
      />

      <Modal
        open={toDelete !== null}
        onClose={() => setToDelete(null)}
        icon="delete"
        size="sm"
        title={t('admin.documents.deleteTitle')}
        footer={
          <>
            <Button variant="ghost" onClick={() => setToDelete(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" isLoading={deleteMutation.isPending} onClick={handleDelete}>
              {t('common.delete')}
            </Button>
          </>
        }
      >
        <p className="font-body-md text-body-md text-on-surface-variant">
          {t('admin.documents.deleteBody', { title: toDelete?.title ?? '' })}
        </p>
      </Modal>
    </div>
  );
}
