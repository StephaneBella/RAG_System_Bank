import { apiClient } from './client';
import { endpoints } from './endpoints';
import type {
  AcceptInvitationRequest,
  CurrentUserResponse,
  LoginRequest,
  TokenResponse,
} from './types';

export async function login(payload: LoginRequest): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>(endpoints.auth.login, payload);
  return data;
}

export async function fetchCurrentUser(): Promise<CurrentUserResponse> {
  const { data } = await apiClient.get<CurrentUserResponse>(endpoints.auth.me);
  return data;
}

export async function logout(): Promise<void> {
  await apiClient.post(endpoints.auth.logout);
}

export async function acceptInvitation(payload: AcceptInvitationRequest): Promise<void> {
  await apiClient.post(endpoints.auth.acceptInvitation, payload);
}
