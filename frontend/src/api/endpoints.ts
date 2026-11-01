/**
 * Single source of truth for every backend route the app relies on.
 */
export const endpoints = {
  auth: {
    login: '/auth/login',
    me: '/auth/me',
    logout: '/auth/logout',
    acceptInvitation: '/auth/accept-invitation',
  },
  departments: {
    list: '/departments',
  },
  documents: {
    list: '/documents/get_all',
    create: '/documents/add',
    update: (id: number) => `/documents/update/${id}`,
    remove: (id: number) => `/documents/delete/${id}`,
    search: '/documents/search_document',
    get: (id: number) => `/documents/${id}`,
    content: (id: number) => `/documents/${id}/content`,
  },
  users: {
    list: '/users',
    createInvitation: '/users/invitations',
    update: (id: number) => `/users/${id}`,
    status: (id: number) => `/users/${id}/status`,
  },
  audit: {
    list: '/audit',
    export: '/audit/export',
  },
} as const;
