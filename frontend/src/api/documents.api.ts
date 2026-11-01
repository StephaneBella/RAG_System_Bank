import { apiClient } from './client';
import { endpoints } from './endpoints';
import type {
  DocumentItem,
  DocumentsQuery,
  Page,
  SearchDocumentsQuery,
  Visibility,
} from './types';

export interface DocumentFormValues {
  title: string;
  department_id: number;
  visibility: Visibility;
  file?: File | null;
}

function toFormData(values: DocumentFormValues): FormData {
  const form = new FormData();
  form.append('title', values.title);
  form.append('department_id', String(values.department_id));
  form.append('visibility', values.visibility);
  if (values.file) form.append('file', values.file);
  return form;
}

export async function listDocuments(params: DocumentsQuery = {}): Promise<Page<DocumentItem>> {
  const { data } = await apiClient.get<Page<DocumentItem>>(endpoints.documents.list, { params });
  return data;
}

export async function searchDocuments(
  params: SearchDocumentsQuery,
): Promise<Page<DocumentItem>> {
  const { data } = await apiClient.get<Page<DocumentItem>>(endpoints.documents.search, {
    params,
  });
  return data;
}

export async function getDocument(id: number): Promise<DocumentItem> {
  const { data } = await apiClient.get<DocumentItem>(endpoints.documents.get(id));
  return data;
}

export async function getDocumentContent(id: number): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(endpoints.documents.content(id), {
    responseType: 'blob',
  });
  return data;
}

export async function createDocument(values: DocumentFormValues): Promise<DocumentItem> {
  const { data } = await apiClient.post<DocumentItem>(endpoints.documents.create, toFormData(values));
  return data;
}

export async function updateDocument(
  id: number,
  values: DocumentFormValues,
): Promise<DocumentItem> {
  const { data } = await apiClient.put<DocumentItem>(
    endpoints.documents.update(id),
    toFormData(values),
  );
  return data;
}

export async function deleteDocument(id: number): Promise<void> {
  await apiClient.delete(endpoints.documents.remove(id));
}
