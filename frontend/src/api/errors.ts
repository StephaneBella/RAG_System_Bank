import axios from 'axios';

export interface ApiValidationIssue {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export function getErrorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined;
}

/** Extracts a human readable message from a FastAPI error payload. */
export function getApiErrorMessage(error: unknown): string | undefined {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error.message : undefined;
  }
  const detail = error.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as ApiValidationIssue;
    if (first?.msg) return first.msg;
  }
  return error.message;
}

export function getValidationIssues(error: unknown): ApiValidationIssue[] {
  if (!axios.isAxiosError(error)) return [];
  const detail = error.response?.data?.detail;
  if (Array.isArray(detail)) return detail as ApiValidationIssue[];
  return [];
}

export function isUnauthorized(error: unknown): boolean {
  return getErrorStatus(error) === 401;
}

export function isForbidden(error: unknown): boolean {
  return getErrorStatus(error) === 403;
}
