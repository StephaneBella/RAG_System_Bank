import { apiClient } from './client';
import { endpoints } from './endpoints';
import type { AuditLog, AuditQuery, Page } from './types';

export async function listAudit(params: AuditQuery = {}): Promise<Page<AuditLog>> {
  const { data } = await apiClient.get<Page<AuditLog>>(endpoints.audit.list, { params });
  return data;
}

export async function exportAudit(params: AuditQuery = {}): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(endpoints.audit.export, {
    params,
    responseType: 'blob',
  });
  return data;
}
