import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { getApiErrorMessage } from '@/api/errors';
import type { CreateUserRequest, User, UserRole } from '@/api/types';
import { RoleBadge, UserStatusBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Icon } from '@/components/ui/Icon';
import { Pagination } from '@/components/ui/Pagination';
import { SearchInput } from '@/components/ui/SearchInput';
import { Select } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';
import { useDebounce } from '@/hooks/useDebounce';
import { UserActivityModal } from '@/components/users/UserActivityModal';
import { UserFormModal } from '@/components/users/UserFormModal';
import { useDepartments } from '@/hooks/useDepartments';
import { useCreateUser, useSetUserStatus, useUpdateUser, useUsers } from '@/hooks/useUsers';
import { initials } from '@/lib/utils';

const PAGE_SIZE = 10;
const ROLES: UserRole[] = ['EMPLOYEE', 'ADMIN'];

type EditorState = { mode: 'create' } | { mode: 'edit'; user: User } | null;

export function AdminUsersPage(): JSX.Element {
  const { t } = useTranslation();
  const toast = useToast();

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<UserRole | ''>('');
  const [departmentFilter, setDepartmentFilter] = useState('');
  const [editor, setEditor] = useState<EditorState>(null);
  const [activityUser, setActivityUser] = useState<User | null>(null);
  const [serverError, setServerError] = useState<string | undefined>();
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const debouncedSearch = useDebounce(search, 300);

  const usersQuery = useUsers({
    page,
    page_size: PAGE_SIZE,
    role: roleFilter || undefined,
    department_id: departmentFilter ? Number(departmentFilter) : undefined,
    search: debouncedSearch.trim() || undefined,
  });
  const departmentsQuery = useDepartments();
  const createMutation = useCreateUser();
  const updateMutation = useUpdateUser();
  const statusMutation = useSetUserStatus();

  const departments = departmentsQuery.data ?? [];
  const rows = usersQuery.data?.items ?? [];

  const clearError = () => setServerError(undefined);

  const handleSubmit = async (payload: CreateUserRequest) => {
    clearError();
    try {
      if (editor?.mode === 'edit') {
        await updateMutation.mutateAsync({ id: editor.user.id, payload });
        toast.success(t('admin.users.updatedToast'));
      } else {
        await createMutation.mutateAsync(payload);
        toast.success(t('admin.users.createdToast'));
      }
      setEditor(null);
    } catch (error) {
      setServerError(getApiErrorMessage(error) ?? t('errors.generic'));
    }
  };

  const handleToggleStatus = async (user: User) => {
    setUpdatingId(user.id);
    try {
      await statusMutation.mutateAsync({ id: user.id, active: user.status !== 'ACTIVE' });
      toast.success(t('admin.users.statusToast'));
    } catch (error) {
      toast.error(getApiErrorMessage(error) ?? t('errors.generic'));
    } finally {
      setUpdatingId(null);
    }
  };

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-headline-lg text-headline-lg font-semibold text-on-surface">
            {t('admin.users.title')}
          </h1>
          <p className="mt-0.5 font-body-md text-body-md text-on-surface-variant">
            {t('admin.users.subtitle')}
          </p>
        </div>
        <Button
          variant="primary"
          icon="person_add"
          onClick={() => {
            clearError();
            setEditor({ mode: 'create' });
          }}
        >
          {t('admin.users.add')}
        </Button>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        <SearchInput
          value={search}
          onChange={(value) => {
            setSearch(value);
            setPage(1);
          }}
          placeholder={t('admin.users.searchPlaceholder')}
          ariaLabel={t('common.search')}
        />
        <Select
          value={roleFilter}
          onChange={(event) => {
            setRoleFilter(event.target.value as UserRole | '');
            setPage(1);
          }}
          className="sm:w-52"
          aria-label={t('admin.users.role')}
        >
          <option value="">{t('admin.users.roleFilter')}</option>
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {t(`roles.${role}`)}
            </option>
          ))}
        </Select>
        <Select
          value={departmentFilter}
          onChange={(event) => {
            setDepartmentFilter(event.target.value);
            setPage(1);
          }}
          className="sm:w-64"
          aria-label={t('admin.users.department')}
        >
          <option value="">{t('admin.users.allDepartments')}</option>
          {departments.map((department) => (
            <option key={department.id} value={department.id}>
              {t(`departments.${department.name}`, { defaultValue: department.name })}
            </option>
          ))}
        </Select>
      </div>

      <Card className="overflow-hidden">
        {usersQuery.isLoading ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <tbody className="divide-y divide-surface-container-high">
                {Array.from({ length: 6 }).map((_, index) => (
                  <tr key={index}>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <Skeleton className="h-9 w-9 rounded-full" />
                        <Skeleton className="h-3.5 w-48" />
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-5 w-20 rounded" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-28" />
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
          <EmptyState icon="group_off" title={t('admin.users.empty')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-hairline bg-surface-container-low">
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.users.columns.name')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.users.columns.role')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.users.columns.department')}
                  </th>
                  <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.users.columns.status')}
                  </th>
                  <th className="px-4 py-3 text-right font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    {t('admin.users.columns.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-high">
                {rows.map((user) => {
                  const isActive = user.status === 'ACTIVE';
                  return (
                    <tr
                      key={user.id}
                      className="transition-colors hover:bg-surface-container-low/50"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2.5">
                          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-container font-label-md text-label-md font-semibold text-primary">
                            {initials(user.email)}
                          </span>
                          <span className="truncate font-body-table text-body-table font-medium text-on-surface">
                            {user.email}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <RoleBadge role={user.role} label={t(`roles.${user.role}`)} />
                      </td>
                      <td className="px-4 py-3 font-body-table text-body-table text-on-surface-variant">
                        {user.department_name}
                      </td>
                      <td className="px-4 py-3">
                        <UserStatusBadge
                          status={user.status}
                          label={t(`statuses.${user.status}`)}
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            type="button"
                            onClick={() => setActivityUser(user)}
                            title={t('admin.users.journal')}
                            className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-primary"
                          >
                            <Icon name="history" className="text-[18px]" />
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              clearError();
                              setEditor({ mode: 'edit', user });
                            }}
                            title={t('common.edit')}
                            className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-primary"
                          >
                            <Icon name="edit" className="text-[18px]" />
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleToggleStatus(user)}
                            disabled={updatingId === user.id}
                            title={isActive ? t('admin.users.deactivate') : t('admin.users.activate')}
                            className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-primary disabled:opacity-50"
                          >
                            {updatingId === user.id ? (
                              <Spinner className="text-[16px] text-primary" />
                            ) : (
                              <Icon
                                name={isActive ? 'block' : 'check_circle'}
                                className="text-[18px]"
                              />
                            )}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <span className="font-body-sm text-body-sm text-on-surface-variant">
        {t('admin.users.foundCount', { count: usersQuery.data?.total ?? 0 })}
      </span>

      {usersQuery.data && (
        <Pagination
          page={usersQuery.data.page}
          pages={usersQuery.data.pages}
          total={usersQuery.data.total}
          onPageChange={setPage}
        />
      )}

      <UserFormModal
        open={editor !== null}
        onClose={() => {
          clearError();
          setEditor(null);
        }}
        user={editor?.mode === 'edit' ? editor.user : null}
        departments={departments}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
        serverError={serverError}
      />

      <UserActivityModal user={activityUser} onClose={() => setActivityUser(null)} />
    </div>
  );
}
