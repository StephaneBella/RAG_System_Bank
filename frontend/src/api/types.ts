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

export type Visibility = 'public' | 'private';

export interface DocumentItem {
  id: number;
  title: string;
  department_id: number;
  department_name: string;
  file_path: string;
  visibility: Visibility;
  created_at: string;
  updated_at: string;
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

export interface InvitationCreated {
  user_id: number;
  email: string;
  invitation_token: string;
  expires_at: string;
}

export interface AcceptInvitationRequest {
  token: string;
  password: string;
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

/** Single source of the audit actions logged by the backend (AuditAction enum). */
export const AUDIT_ACTIONS = [
  'LOGIN',
  'LOGOUT',
  'DOCUMENT_VIEW',
  'DOCUMENT_SEARCH',
  'DOCUMENT_CREATE',
  'DOCUMENT_UPDATE',
  'DOCUMENT_DELETE',
  'USER_CREATE',
  'USER_UPDATE',
  'USER_STATUS_UPDATE',
  'EXPORT',
  'AUTHENTICATION_FAILED',
  'ACCESS_DENIED',
  'SESSION_EXPIRED',
  'TOKEN_MISSING',
  'LLM_CALL',
] as const;

export type AuditAction = (typeof AUDIT_ACTIONS)[number];

export type AuditResourceType =
  | 'DOCUMENT'
  | 'USER'
  | 'SESSION'
  | 'AUDIT_LOG'
  | 'DEPARTMENT'
  | 'LLM_GATEWAY';

export type AuditResult = 'SUCCESS' | 'FAILURE';

/** Why an extract was kept out of the LLM context (backend ExclusionReason). */
export type ContextExclusionReason =
  | 'EMPTY_TEXT'
  | 'MISSING_SOURCE'
  | 'EXCLUDED_SOURCE'
  | 'MISSING_LAYER'
  | 'LAYER_NOT_ALLOWED'
  | 'DUPLICATE'
  | 'MAX_EXTRACTS'
  | 'CONTEXT_BUDGET';

/**
 * Closed list of the gateway fields logged with LLM_CALL (backend services/audit.py):
 * enum values and counters only, never the question, prompt, answer or sources.
 */
export interface AuditLlmCallDetails {
  outcome: 'ANSWERED' | 'NO_AUTHORIZED_CONTEXT' | 'PROMPT_TOO_LONG' | 'FAILED';
  failed_stage: 'QUESTION' | 'RETRIEVAL' | 'CONTEXT' | 'ASSEMBLY' | 'PROVIDER' | 'RESTORE' | null;
  error_type: string | null;
  received: number | null;
  retained: number | null;
  excluded: Partial<Record<ContextExclusionReason, number>>;
  prompt_chars: number | null;
  answer_chars: number | null;
  tokens_restored: number;
  tokens_withheld: number;
  gateway_ms: number;
}

export interface AuditLog {
  id: number;
  user_id: number | null;
  user_email: string | null;
  action: AuditAction;
  resource_type: AuditResourceType;
  resource_id: number | null;
  result: AuditResult;
  ip_address: string | null;
  timestamp: string;
  user_department_id?: number | null;
  user_department_name?: string | null;
  resource_department_id?: number | null;
  resource_department_name?: string | null;
  duration_ms?: number | null;
  details?: { reason?: string; document_ids?: number[]; llm_call?: AuditLlmCallDetails } | null;
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
