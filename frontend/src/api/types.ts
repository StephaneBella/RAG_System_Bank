export type UserRole = 'ADMIN' | 'EMPLOYEE';
export type UserStatus = 'ACTIVE' | 'INACTIVE';

export interface Department {
  id: number;
  name: string;
}

export interface User {
  id: number;
  email: string;
  role: UserRole;
  status: UserStatus;
  department_id: number;
  department_name: string;
}

export type CurrentUserResponse = User;

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export type Classification = 'public' | 'private' | 'confidential';

export interface DocumentItem {
  id: number;
  title: string;
  department_id: number;
  department_name: string;
  classification?: Classification;
  file_name?: string;
  file_path?: string;
  file_size?: number;
  mime_type?: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
}

export interface SortOrder {
  sort_by?: string;
  order?: 'asc' | 'desc';
}

export interface DocumentsQuery extends SortOrder {
  page?: number;
  page_size?: number;
}

export interface SearchDocumentsQuery extends DocumentsQuery {
  q: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface CreateUserRequest {
  email: string;
  role: UserRole;
  department_id: number;
}

export interface UpdateUserRequest {
  email?: string;
  role?: UserRole;
  department_id?: number;
}

export interface UsersQuery extends SortOrder {
  page?: number;
  page_size?: number;
  role?: UserRole;
  department_id?: number;
  search?: string;
}

export type AuditAction =
  | 'LOGIN'
  | 'LOGOUT'
  | 'DOCUMENT_VIEW'
  | 'DOCUMENT_CREATE'
  | 'DOCUMENT_UPDATE'
  | 'DOCUMENT_DELETE'
  | 'USER_CREATE'
  | 'USER_UPDATE'
  | 'USER_STATUS_UPDATE'
  | 'EXPORT';

export type AuditResult = 'SUCCESS' | 'FAILURE';

export interface AuditLog {
  id: number;
  user_id: number;
  user_email: string;
  action: AuditAction;
  resource_type: 'DOCUMENT' | 'USER' | 'SESSION';
  resource_id: number;
  resource_label?: string;
  result: AuditResult;
  ip_address?: string;
  context?: string;
  timestamp: string;
}

export interface AuditQuery {
  page?: number;
  page_size?: number;
  action?: AuditAction | 'ALL';
  result?: AuditResult | 'ALL';
  user_email?: string;
  period?: 'all' | 'today' | 'last7' | 'last30' | 'month';
  date_from?: string;
  date_to?: string;
}
