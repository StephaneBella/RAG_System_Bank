import { http, HttpResponse } from 'msw';

import { decodeJwtPayload, delay } from '@/lib/utils';

import {
  auditLogs,
  departments,
  documents,
  mockDb,
  publicUser,
  users,
  type MockUser,
} from './data';
import type {
  AuditAction,
  AuditLog,
  AuditResult,
  DocumentItem,
  UserRole,
} from '@/api/types';

const TOKEN_TTL_SECONDS = 60 * 60;

const base64Url = (value: object): string =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

function issueToken(user: MockUser): string {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'HS256', typ: 'JWT' };
  const payload = {
    sub: String(user.id),
    role: user.role,
    iat: now,
    exp: now + TOKEN_TTL_SECONDS,
    jti: `mock-${user.id}-${now}`,
  };
  return `${base64Url(header)}.${base64Url(payload)}.mocksignature`;
}

function currentUser(request: Request): MockUser | null {
  const header = request.headers.get('Authorization') ?? '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : '';
  if (!token) return null;
  const payload = decodeJwtPayload(token);
  if (!payload) return null;
  const exp = payload.exp;
  if (typeof exp === 'number' && exp * 1000 < Date.now()) return null;
  const user = users.find((candidate) => candidate.id === Number(payload.sub));
  if (!user || user.status !== 'ACTIVE') return null;
  return user;
}

const unauthorized = () =>
  HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 });

const forbidden = () =>
  HttpResponse.json({ detail: 'Permission denied' }, { status: 403 });

function isAdmin(user: MockUser): boolean {
  return user.role === 'ADMIN';
}

function scopedDocuments(user: MockUser): DocumentItem[] {
  if (user.role === 'EMPLOYEE') {
    return documents.filter((doc) => doc.department_id === user.department_id);
  }
  return documents;
}

function sortByField<T>(items: T[], field: string, order: string): T[] {
  const dir = order === 'asc' ? 1 : -1;
  return [...items].sort((a, b) => {
    const av = (a as Record<string, unknown>)[field];
    const bv = (b as Record<string, unknown>)[field];
    if (av === bv) return 0;
    if (av === undefined || av === null) return 1;
    if (bv === undefined || bv === null) return -1;
    return (av > bv ? 1 : -1) * dir;
  });
}

function paginate<T>(items: T[], page: number, pageSize: number) {
  const safePage = Math.max(1, page);
  const safeSize = Math.max(1, pageSize);
  const total = items.length;
  const pages = total === 0 ? 0 : Math.ceil(total / safeSize);
  const start = (safePage - 1) * safeSize;
  const slice = items.slice(start, start + safeSize);
  return {
    items: slice,
    total,
    page: safePage,
    page_size: safeSize,
    pages,
    has_next: safePage < pages,
    has_prev: safePage > 1,
  };
}

function departmentName(id: number): string {
  return departments.find((dept) => dept.id === id)?.name ?? 'Inconnu';
}

const isoNow = (): string => new Date().toISOString();

function buildDocument(
  id: number,
  form: FormData,
  existing?: DocumentItem,
): DocumentItem {
  const title = String(form.get('title') ?? existing?.title ?? '');
  const departmentId = Number(form.get('department_id') ?? existing?.department_id ?? 0);
  const classification = (form.get('classification')
    ? String(form.get('classification'))
    : existing?.classification ?? 'private') as DocumentItem['classification'];
  const description = form.get('description')
    ? String(form.get('description'))
    : existing?.description;
  const file = form.get('file');
  const hasFile = typeof file === 'object' && file !== null && 'size' in file;
  const storedVisibility = classification === 'public' ? 'public' : 'private';

  return {
    id,
    title,
    department_id: departmentId,
    department_name: departmentName(departmentId),
    classification,
    file_name: `${storedVisibility}__${departmentName(departmentId).toLowerCase().replace(/\s+/g, '_')}__${title
      .toLowerCase()
      .replace(/\s+/g, '_')}.pdf`,
    file_size: hasFile ? (file as File).size : (existing?.file_size ?? 0),
    mime_type: hasFile ? (file as File).type || 'application/pdf' : existing?.mime_type,
    description: description ?? undefined,
    created_at: existing?.created_at ?? isoNow(),
    updated_at: isoNow(),
  };
}

function audit(
  user: MockUser,
  action: AuditAction,
  resourceType: AuditLog['resource_type'],
  resourceId: number,
  resourceLabel: string,
  result: AuditResult = 'SUCCESS',
): void {
  auditLogs.unshift({
    id: mockDb.nextAuditId(),
    user_id: user.id,
    user_email: user.email,
    action,
    resource_type: resourceType,
    resource_id: resourceId,
    resource_label: resourceLabel,
    result,
    ip_address: '192.168.10.1',
    context: 'Siège Paris (LAN Interne)',
    timestamp: isoNow(),
  });
}

export const handlers = [
  // ---- Auth -----------------------------------------------------------------
  http.post('/auth/login', async ({ request }) => {
    await delay(500);
    const body = (await request.json()) as { email?: string; password?: string };
    const user = users.find(
      (candidate) => candidate.email.toLowerCase() === (body.email ?? '').toLowerCase(),
    );
    if (!user || user.password !== body.password || user.status !== 'ACTIVE') {
      return HttpResponse.json({ detail: 'Invalid email or password' }, { status: 401 });
    }
    audit(user, 'LOGIN', 'SESSION', 0, 'Portail — Authentification');
    return HttpResponse.json({ access_token: issueToken(user), token_type: 'bearer' });
  }),

  http.get('/auth/me', ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    return HttpResponse.json(publicUser(user));
  }),

  http.post('/auth/logout', ({ request }) => {
    const user = currentUser(request);
    if (user) audit(user, 'LOGOUT', 'SESSION', 0, 'Fermeture de session');
    return HttpResponse.json({ message: 'Successfully logged out' });
  }),

  // ---- Departments ----------------------------------------------------------
  http.get('/departments', ({ request }) => {
    if (!currentUser(request)) return unauthorized();
    return HttpResponse.json(departments);
  }),

  // ---- Documents ------------------------------------------------------------
  http.get('/documents/get_all', ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    const url = new URL(request.url);
    const page = Number(url.searchParams.get('page') ?? 1);
    const pageSize = Number(url.searchParams.get('page_size') ?? 20);
    const sortField = url.searchParams.get('sort_by') ?? 'created_at';
    const order = url.searchParams.get('order') ?? 'desc';
    const items = sortByField(scopedDocuments(user), sortField, order);
    return HttpResponse.json(paginate(items, page, pageSize));
  }),

  http.get('/documents/search_document', ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    const url = new URL(request.url);
    const needle = (url.searchParams.get('q') ?? '').toLowerCase();
    const page = Number(url.searchParams.get('page') ?? 1);
    const pageSize = Number(url.searchParams.get('page_size') ?? 20);
    const sortField = url.searchParams.get('sort_by') ?? 'created_at';
    const order = url.searchParams.get('order') ?? 'desc';
    const matches = scopedDocuments(user).filter((doc) =>
      `${doc.title} ${doc.description ?? ''}`.toLowerCase().includes(needle),
    );
    const items = sortByField(matches, sortField, order);
    return HttpResponse.json(paginate(items, page, pageSize));
  }),

  http.get('/documents/get/:id', ({ request, params }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    const doc = documents.find((item) => item.id === Number(params.id));
    if (!doc) return HttpResponse.json({ detail: 'Document not found' }, { status: 404 });
    if (user.role === 'EMPLOYEE' && doc.department_id !== user.department_id) {
      return forbidden();
    }
    audit(user, 'DOCUMENT_VIEW', 'DOCUMENT', doc.id, doc.title);
    return HttpResponse.json(doc);
  }),

  http.post('/documents/add', async ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const form = await request.formData();
    const doc = buildDocument(mockDb.nextDocumentId(), form);
    documents.unshift(doc);
    audit(user, 'DOCUMENT_CREATE', 'DOCUMENT', doc.id, doc.title);
    return HttpResponse.json(doc, { status: 201 });
  }),

  http.put('/documents/update/:id', async ({ request, params }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const index = documents.findIndex((item) => item.id === Number(params.id));
    if (index < 0) return HttpResponse.json({ detail: 'Document not found' }, { status: 404 });
    const form = await request.formData();
    const updated = buildDocument(documents[index].id, form, documents[index]);
    documents[index] = updated;
    audit(user, 'DOCUMENT_UPDATE', 'DOCUMENT', updated.id, updated.title);
    return HttpResponse.json(updated);
  }),

  http.delete('/documents/delete/:id', ({ request, params }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const index = documents.findIndex((item) => item.id === Number(params.id));
    if (index < 0) return HttpResponse.json({ detail: 'Document not found' }, { status: 404 });
    const [removed] = documents.splice(index, 1);
    audit(user, 'DOCUMENT_DELETE', 'DOCUMENT', removed.id, removed.title);
    return HttpResponse.json({ detail: 'Document deleted successfully' });
  }),

  // ---- Users ----------------------------------------------------------------
  http.get('/users', ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const url = new URL(request.url);
    const page = Number(url.searchParams.get('page') ?? 1);
    const pageSize = Number(url.searchParams.get('page_size') ?? 20);
    const role = url.searchParams.get('role') as UserRole | null;
    const departmentId = url.searchParams.get('department_id');
    const search = (url.searchParams.get('search') ?? '').trim().toLowerCase();
    let items = users.filter((candidate) => {
      if (role && candidate.role !== role) return false;
      if (departmentId && candidate.department_id !== Number(departmentId)) return false;
      if (search && !candidate.email.toLowerCase().includes(search)) return false;
      return true;
    });
    items = sortByField(items, 'id', 'asc');
    return HttpResponse.json(paginate(items.map(publicUser), page, pageSize));
  }),

  http.post('/users', async ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const body = (await request.json()) as {
      email: string;
      role: UserRole;
      department_id: number;
    };
    const newId = Math.max(0, ...users.map((candidate) => candidate.id)) + 1;
    const created: MockUser = {
      id: newId,
      email: body.email,
      password: 'Password123',
      role: body.role,
      status: 'ACTIVE',
      department_id: body.department_id,
      department_name: departmentName(body.department_id),
    };
    users.push(created);
    audit(user, 'USER_CREATE', 'USER', created.id, created.email);
    return HttpResponse.json(publicUser(created), { status: 201 });
  }),

  http.patch('/users/:id', async ({ request, params }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const target = users.find((candidate) => candidate.id === Number(params.id));
    if (!target) return HttpResponse.json({ detail: 'User not found' }, { status: 404 });
    const body = (await request.json()) as Partial<{
      email: string;
      role: UserRole;
      department_id: number;
    }>;
    Object.assign(target, body);
    if (body.department_id) target.department_name = departmentName(body.department_id);
    audit(user, 'USER_UPDATE', 'USER', target.id, target.email);
    return HttpResponse.json(publicUser(target));
  }),

  http.post('/users/:id/status', async ({ request, params }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const target = users.find((candidate) => candidate.id === Number(params.id));
    if (!target) return HttpResponse.json({ detail: 'User not found' }, { status: 404 });
    const body = (await request.json()) as { active: boolean };
    target.status = body.active ? 'ACTIVE' : 'INACTIVE';
    audit(user, 'USER_STATUS_UPDATE', 'USER', target.id, target.email);
    return HttpResponse.json(publicUser(target));
  }),

  // ---- Audit ----------------------------------------------------------------
  http.get('/audit', ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const url = new URL(request.url);
    const page = Number(url.searchParams.get('page') ?? 1);
    const pageSize = Number(url.searchParams.get('page_size') ?? 20);
    const action = url.searchParams.get('action');
    const result = url.searchParams.get('result');
    const period = url.searchParams.get('period') ?? 'all';
    const userEmail = url.searchParams.get('user_email');

    let items = [...auditLogs];
    if (action && action !== 'ALL') items = items.filter((log) => log.action === action);
    if (result && result !== 'ALL') items = items.filter((log) => log.result === result);
    if (userEmail) {
      const needle = userEmail.toLowerCase();
      items = items.filter((log) => log.user_email.toLowerCase().includes(needle));
    }

    const days = { today: 1, last7: 7, last30: 30, month: 30 } as Record<string, number>;
    if (period !== 'all' && days[period]) {
      const cutoff = Date.now() - days[period] * 86_400_000;
      items = items.filter((log) => new Date(log.timestamp).getTime() >= cutoff);
    }

    items = sortByField(items, 'timestamp', 'desc');
    return HttpResponse.json(paginate(items, page, pageSize));
  }),

  http.get('/audit/export', ({ request }) => {
    const user = currentUser(request);
    if (!user) return unauthorized();
    if (!isAdmin(user)) return forbidden();
    const url = new URL(request.url);
    const action = url.searchParams.get('action');
    const result = url.searchParams.get('result');
    const userEmail = url.searchParams.get('user_email');
    const days = { today: 1, last7: 7, last30: 30, month: 30 } as Record<string, number>;
    const period = url.searchParams.get('period') ?? 'all';

    let items = [...auditLogs];
    if (action && action !== 'ALL') items = items.filter((log) => log.action === action);
    if (result && result !== 'ALL') items = items.filter((log) => log.result === result);
    if (userEmail) {
      const needle = userEmail.toLowerCase();
      items = items.filter((log) => log.user_email.toLowerCase().includes(needle));
    }
    if (period !== 'all' && days[period]) {
      const cutoff = Date.now() - days[period] * 86_400_000;
      items = items.filter((log) => new Date(log.timestamp).getTime() >= cutoff);
    }

    const header = 'id,date,email,action,ressource,resultat,ip,contexte\n';
    const rows = items
      .map((log) =>
        [
          log.id,
          log.timestamp,
          log.user_email,
          log.action,
          (log.resource_label ?? '').replace(/,/g, ' '),
          log.result,
          log.ip_address ?? '',
          (log.context ?? '').replace(/,/g, ' '),
        ].join(','),
      )
      .join('\n');
    audit(user, 'EXPORT', 'DOCUMENT', 0, 'Export du journal d’audit (CSV)');
    return new HttpResponse(header + rows, {
      headers: {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': 'attachment; filename="audit-log.csv"',
      },
    });
  }),
];
