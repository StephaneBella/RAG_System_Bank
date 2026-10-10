import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Classification, Department, DocumentItem } from '@/api/types';
import type { DocumentFormValues } from '@/api/documents.api';
import { Button } from '@/components/ui/Button';
import { FileDropzone } from '@/components/ui/FileDropzone';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';

const CLASSIFICATIONS: Classification[] = ['public', 'private', 'confidential'];

interface DocumentFormModalProps {
  open: boolean;
  onClose: () => void;
  document?: DocumentItem | null;
  departments: Department[];
  onSubmit: (values: DocumentFormValues) => void;
  isSubmitting?: boolean;
  serverError?: string;
}

export function DocumentFormModal({
  open,
  onClose,
  document,
  departments,
  onSubmit,
  isSubmitting = false,
  serverError,
}: DocumentFormModalProps): JSX.Element {
  const { t } = useTranslation();
  const isEdit = Boolean(document);

  const [title, setTitle] = useState('');
  const [departmentId, setDepartmentId] = useState('');
  const [classification, setClassification] = useState<Classification>('private');
  const [description, setDescription] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    setTitle(document?.title ?? '');
    setDepartmentId(document ? String(document.department_id) : '');
    setClassification(document?.classification ?? 'private');
    setDescription(document?.description ?? '');
    setFile(null);
    setErrors({});
  }, [open, document]);

  const handleSubmit = () => {
    const nextErrors: Record<string, string> = {};
    if (!title.trim()) nextErrors.title = t('admin.documents.requiredTitle');
    if (!departmentId) nextErrors.department = t('admin.documents.requiredDepartment');
    if (!isEdit && !file) nextErrors.file = t('admin.documents.requiredFile');
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    onSubmit({
      title: title.trim(),
      department_id: Number(departmentId),
      classification,
      description: description.trim() || undefined,
      file,
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      icon={isEdit ? 'edit_document' : 'note_add'}
      title={isEdit ? t('admin.documents.edit') : t('admin.documents.newTitle')}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            icon="save"
            isLoading={isSubmitting}
            onClick={handleSubmit}
          >
            {isEdit ? t('common.save') : t('admin.documents.create')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Input
          id="doc-title"
          label={`${t('admin.documents.formTitle')} *`}
          placeholder={t('admin.documents.formTitlePlaceholder')}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          error={errors.title}
        />

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Select
            id="doc-department"
            label={`${t('admin.documents.formDepartment')} *`}
            value={departmentId}
            onChange={(event) => setDepartmentId(event.target.value)}
            error={errors.department}
          >
            <option value="">{t('admin.documents.selectDepartment')}</option>
            {departments.map((department) => (
              <option key={department.id} value={department.id}>
                {t(`departments.${department.name}`, { defaultValue: department.name })}
              </option>
            ))}
          </Select>

          <Select
            id="doc-classification"
            label={t('admin.documents.formClassification')}
            value={classification}
            onChange={(event) => setClassification(event.target.value as Classification)}
          >
            {CLASSIFICATIONS.map((value) => (
              <option key={value} value={value}>
                {t(`classifications.${value}`)}
              </option>
            ))}
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="font-label-md text-label-md text-on-surface">
            {t('admin.documents.formFile')} {!isEdit && '*'}
          </span>
          <FileDropzone
            file={file}
            onFileChange={setFile}
            dropHint={t('admin.documents.dropHint')}
            formatsHint={t('admin.documents.formatsHint')}
            selectedLabel={t('admin.documents.fileSelected')}
            error={errors.file}
          />
        </div>

        <Textarea
          id="doc-description"
          label={t('admin.documents.formDescription')}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />

        {serverError && (
          <div className="flex items-center gap-2 rounded-lg border border-[#fca5a5] bg-[#fef2f2] px-3 py-2 text-body-sm text-[#991b1b]">
            {serverError}
          </div>
        )}
      </div>
    </Modal>
  );
}
