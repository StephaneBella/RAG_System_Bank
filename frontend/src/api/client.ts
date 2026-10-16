import axios, { type AxiosError } from 'axios';

import { clearToken, getToken } from './tokenStore';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;
let isHandlingUnauthorized = false;

/** Registered by `AuthProvider` to react to an expired / revoked session. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

function isAuthRequest(url?: string): boolean {
  if (!url) return false;
  return url.includes('/auth/login') || url.includes('/auth/logout');
}

apiClient.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status;
    if (status === 401 && !isAuthRequest(error.config?.url)) {
      clearToken();
      // Single-flight: a burst of parallel 401s must trigger one logout.
      if (!isHandlingUnauthorized) {
        isHandlingUnauthorized = true;
        unauthorizedHandler?.();
        window.setTimeout(() => {
          isHandlingUnauthorized = false;
        }, 0);
      }
    }
    return Promise.reject(error);
  },
);
