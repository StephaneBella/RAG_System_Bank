/**
 * Single source of truth for every backend route the app relies on.
 *
 * Routes marked "existing" are already implemented in the FastAPI backend.
 * The rest are the contract the backend must fulfil for this frontend sprint;
 * until then they are served by MSW mocks (see `src/mocks/handlers.ts`).
 */
export const endpoints = {
  auth: {
    login: '/auth/login', // existing
    me: '/auth/me', // existing
    logout: '/auth/logout', // existing
  },
  departments: {
    list: '/departments', // TODO backend
  },
  documents: {
    list: '/documents/get_all', // existing
    create: '/documents/add', // existing (multipart)
    update: (id: number) => `/documents/update/${id}`, // existing (multipart)
    remove: (id: number) => `/documents/delete/${id}`, // existing
    search: '/documents/search_document', // existing
    get: (id: number) => `/documents/get/${id}`, // TODO backend
  },
  users: {
    list: '/users', // TODO backend
    create: '/users', // TODO backend
    update: (id: number) => `/users/${id}`, // TODO backend
    status: (id: number) => `/users/${id}/status`, // TODO backend
  },
  audit: {
    list: '/audit', // TODO backend
    export: '/audit/export', // TODO backend
  },
} as const;
