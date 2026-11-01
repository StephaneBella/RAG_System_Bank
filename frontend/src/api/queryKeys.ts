import type { AuditQuery, DocumentsQuery, SearchDocumentsQuery, UsersQuery } from './types';

export const queryKeys = {
  currentUser: ['auth', 'me'] as const,
  departments: ['departments'] as const,
  documents: {
    all: ['documents'] as const,
    list: (params: DocumentsQuery) => ['documents', 'list', params] as const,
    search: (params: SearchDocumentsQuery) => ['documents', 'search', params] as const,
    detail: (id: number) => ['documents', 'detail', id] as const,
    content: (id: number) => ['documents', 'content', id] as const,
  },
  users: {
    all: ['users'] as const,
    list: (params: UsersQuery) => ['users', 'list', params] as const,
  },
  audit: {
    all: ['audit'] as const,
    list: (params: AuditQuery) => ['audit', 'list', params] as const,
  },
};
