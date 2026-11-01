import { apiClient } from './client';
import { endpoints } from './endpoints';
import type { Department } from './types';

export async function fetchDepartments(): Promise<Department[]> {
  const { data } = await apiClient.get<Department[]>(endpoints.departments.list);
  return data;
}
