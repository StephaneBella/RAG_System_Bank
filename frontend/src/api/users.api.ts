import { apiClient } from './client';
import { endpoints } from './endpoints';
import type {
  CreateUserRequest,
  Page,
  UpdateUserRequest,
  User,
  UsersQuery,
} from './types';

export async function listUsers(params: UsersQuery = {}): Promise<Page<User>> {
  const { data } = await apiClient.get<Page<User>>(endpoints.users.list, { params });
  return data;
}

export async function createUser(payload: CreateUserRequest): Promise<User> {
  const { data } = await apiClient.post<User>(endpoints.users.create, payload);
  return data;
}

export async function updateUser(id: number, payload: UpdateUserRequest): Promise<User> {
  const { data } = await apiClient.patch<User>(endpoints.users.update(id), payload);
  return data;
}

export async function setUserStatus(id: number, active: boolean): Promise<User> {
  const { data } = await apiClient.post<User>(endpoints.users.status(id), { active });
  return data;
}
