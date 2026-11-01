import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { CreateUserRequest, Department, User, UserRole } from '@/api/types';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';

const ROLES: UserRole[] = ['EMPLOYEE', 'ADMIN'];

interface UserFormModalProps {
  open: boolean;
  onClose: () => void;
  user?: User | null;
  departments: Department[];
  onSubmit: (payload: CreateUserRequest) => void;
  isSubmitting?: boolean;
  serverError?: string;
}

export function UserFormModal({
  open,
  onClose,
  user,
  departments,
  onSubmit,
  isSubmitting = false,
  serverError,
}: UserFormModalProps): JSX.Element {
  const { t } = useTranslation();
  const isEdit = Boolean(user);

  const [email, setEmail] = useState('');
  const [role, setRole] = useState<UserRole>('EMPLOYEE');
  const [departmentId, setDepartmentId] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    setEmail(user?.email ?? '');
    setRole(user?.role ?? 'EMPLOYEE');
    setDepartmentId(user ? String(user.department_id) : '');
    setErrors({});
  }, [open, user]);

  const handleSubmit = () => {
    const nextErrors: Record<string, string> = {};
    if (!email.trim()) nextErrors.email = t('validation.emailRequired');
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()))
      nextErrors.email = t('validation.emailInvalid');
    if (!departmentId) nextErrors.department_id = t('validation.required');
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    onSubmit({
      email: email.trim(),
      role,
      department_id: Number(departmentId),
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      icon={isEdit ? 'manage_accounts' : 'person_add'}
      title={isEdit ? t('admin.users.editTitle') : t('admin.users.newTitle')}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" icon="save" isLoading={isSubmitting} onClick={handleSubmit}>
            {isEdit ? t('common.save') : t('admin.users.create')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Input
          id="user-email"
          type="email"
          label={t('admin.users.email')}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={errors.email}
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Select
            id="user-role"
            label={t('admin.users.role')}
            value={role}
            onChange={(event) => setRole(event.target.value as UserRole)}
          >
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {t(`roles.${value}`)}
              </option>
            ))}
          </Select>
          <Select
            id="user-department"
            label={t('admin.users.department')}
            value={departmentId}
            onChange={(event) => setDepartmentId(event.target.value)}
            error={errors.department_id}
          >
            <option value="">{t('admin.documents.selectDepartment')}</option>
            {departments.map((department) => (
              <option key={department.id} value={department.id}>
                {t(`departments.${department.name}`, { defaultValue: department.name })}
              </option>
            ))}
          </Select>
        </div>

        {serverError && (
          <div className="flex items-center gap-2 rounded-lg border border-[#fca5a5] bg-[#fef2f2] px-3 py-2 text-body-sm text-[#991b1b]">
            {serverError}
          </div>
        )}
      </div>
    </Modal>
  );
}
