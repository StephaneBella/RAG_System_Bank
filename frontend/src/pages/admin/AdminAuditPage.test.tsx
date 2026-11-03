import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AUDIT_ACTIONS, type AuditLog, type AuditQuery } from '@/api/types';
import { ToastProvider } from '@/components/ui/Toast';
import { useAudit } from '@/hooks/useAudit';
import i18n from '@/i18n';

import { AdminAuditPage } from './AdminAuditPage';

// No network: the audit query and the auth context are simulated.
vi.mock('@/hooks/useAudit', () => ({ useAudit: vi.fn() }));
vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, isAdmin: true }),
}));

const mockedUseAudit = vi.mocked(useAudit);
const NEW_ACTIONS = ['TOKEN_MISSING', 'AUTHENTICATION_FAILED', 'SESSION_EXPIRED', 'ACCESS_DENIED'];
const RESOURCE_TYPES = ['DOCUMENT', 'USER', 'SESSION', 'AUDIT_LOG', 'DEPARTMENT', 'LLM_GATEWAY'];

function log(overrides: Partial<AuditLog>): AuditLog {
  return {
    id: 1,
    user_id: 7,
    user_email: 'employee@example.com',
    action: 'LOGIN',
    resource_type: 'SESSION',
    resource_id: null,
    result: 'FAILURE',
    ip_address: '203.0.113.7',
    timestamp: '2026-11-02T10:00:00',
    ...overrides,
  };
}

function mockRows(rows: AuditLog[]) {
  mockedUseAudit.mockImplementation((params: AuditQuery = {}) => {
    const page = params.page ?? 1;
    return {
      data: {
        items: rows,
        total: 60,
        page,
        page_size: 20,
        pages: 3,
        has_next: page < 3,
        has_prev: page > 1,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useAudit>;
  });
}

function lastParams(): AuditQuery {
  const call = mockedUseAudit.mock.calls.at(-1);
  if (!call) throw new Error('useAudit was not called');
  return call[0] ?? {};
}

function renderPage() {
  return render(
    <ToastProvider>
      <AdminAuditPage />
    </ToastProvider>,
  );
}

const fr = i18n.getFixedT('fr');

function actionSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', { name: fr('admin.audit.columns.event') });
}

function goToPage2() {
  fireEvent.click(screen.getByRole('button', { name: fr('common.next') }));
  expect(lastParams().page).toBe(2);
}

beforeEach(async () => {
  mockedUseAudit.mockReset();
  mockRows([]);
  await i18n.changeLanguage('fr');
});

describe('audit translations', () => {
  it.each(['fr', 'en'])('translates the 16 audit actions in %s', (lng) => {
    expect(AUDIT_ACTIONS).toHaveLength(16);
    for (const action of AUDIT_ACTIONS) {
      // getResource ignores the fallback language: each locale must define the key itself.
      const label = i18n.getResource(lng, 'translation', `auditActions.${action}`);
      expect(typeof label, `${lng} auditActions.${action}`).toBe('string');
      expect(label).not.toBe(action);
    }
  });

  it.each(['fr', 'en'])('translates resource types and new audit labels in %s', (lng) => {
    for (const type of RESOURCE_TYPES) {
      expect(i18n.getResource(lng, 'translation', `resourceTypes.${type}`)).toEqual(
        expect.any(String),
      );
    }
    for (const key of ['anonymous', 'dateFrom', 'dateTo']) {
      expect(i18n.getResource(lng, 'translation', `admin.audit.${key}`)).toEqual(
        expect.any(String),
      );
    }
  });
});

describe('LLM gateway audit labels', () => {
  it.each([
    ['fr', 'Appel au LLM', 'Passerelle LLM'],
    ['en', 'LLM call', 'LLM gateway'],
  ])('labels LLM_CALL and LLM_GATEWAY in %s', (lng, action, resource) => {
    expect(i18n.getResource(lng, 'translation', 'auditActions.LLM_CALL')).toBe(action);
    expect(i18n.getResource(lng, 'translation', 'resourceTypes.LLM_GATEWAY')).toBe(resource);
  });
});

describe('AdminAuditPage', () => {
  it('offers the 16 actions in the event filter', () => {
    renderPage();
    const options = Array.from(actionSelect().options);
    expect(options.map((option) => option.value)).toEqual(['ALL', ...AUDIT_ACTIONS]);
    for (const option of options) {
      expect(option.textContent).not.toMatch(/^auditActions\./);
    }
  });

  it('displays the four new actions with their translated labels', () => {
    mockRows(
      NEW_ACTIONS.map((action, index) =>
        log({ id: index + 1, action: action as AuditLog['action'] }),
      ),
    );
    renderPage();
    const table = screen.getByRole('table');
    for (const action of NEW_ACTIONS) {
      expect(table).toHaveTextContent(fr(`auditActions.${action}`));
    }
    expect(table.textContent).not.toMatch(/auditActions\./);
  });

  it('never shows a raw translation key for an unknown action', () => {
    mockRows([log({ action: 'FUTURE_ACTION' as AuditLog['action'] })]);
    renderPage();
    const table = screen.getByRole('table');
    expect(table).toHaveTextContent('FUTURE_ACTION');
    expect(table.textContent).not.toMatch(/auditActions\./);
  });

  it('filters on a new action and goes back to page 1', () => {
    mockRows([log({})]);
    renderPage();
    goToPage2();

    fireEvent.change(actionSelect(), { target: { value: 'ACCESS_DENIED' } });

    expect(lastParams()).toMatchObject({ action: 'ACCESS_DENIED', page: 1 });
  });

  it('shows "Anonyme" for an event without user', () => {
    mockRows([log({ user_id: null, user_email: null, action: 'TOKEN_MISSING' })]);
    renderPage();
    expect(screen.getByRole('table')).toHaveTextContent(fr('admin.audit.anonymous'));
    expect(fr('admin.audit.anonymous')).toBe('Anonyme');
  });

  it('sends the custom date range as inclusive date_from / date_to', () => {
    mockRows([log({})]);
    renderPage();
    expect(lastParams().date_from).toBeUndefined();
    expect(lastParams().date_to).toBeUndefined();
    goToPage2();

    fireEvent.change(screen.getByLabelText('Du'), { target: { value: '2026-11-01' } });
    expect(lastParams()).toMatchObject({ date_from: '2026-11-01T00:00:00', page: 1 });
    expect(lastParams().date_to).toBeUndefined();

    goToPage2();
    fireEvent.change(screen.getByLabelText('Au'), { target: { value: '2026-11-02' } });
    expect(lastParams()).toMatchObject({
      date_from: '2026-11-01T00:00:00',
      date_to: '2026-11-02T23:59:59.999999',
      page: 1,
    });
  });

  it('keeps preset periods and the custom date range exclusive', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: fr('admin.audit.today') }));
    expect(lastParams().period).toBe('today');

    fireEvent.change(screen.getByLabelText('Du'), { target: { value: '2026-11-01' } });
    expect(lastParams()).toMatchObject({ period: 'all', date_from: '2026-11-01T00:00:00' });

    fireEvent.click(screen.getByRole('button', { name: fr('admin.audit.last7') }));
    expect(lastParams().period).toBe('last7');
    expect(lastParams().date_from).toBeUndefined();
    expect(screen.getByLabelText('Du')).toHaveValue('');
  });

  it('clears the custom date range when "all dates" is selected', () => {
    mockRows([log({})]);
    renderPage();
    fireEvent.change(screen.getByLabelText('Du'), { target: { value: '2026-11-01' } });
    fireEvent.change(screen.getByLabelText('Au'), { target: { value: '2026-11-02' } });
    expect(lastParams()).toMatchObject({ period: 'all', date_from: '2026-11-01T00:00:00' });
    goToPage2();

    fireEvent.click(screen.getByRole('button', { name: fr('admin.audit.allDates') }));

    expect(lastParams()).toMatchObject({ period: 'all', page: 1 });
    expect(lastParams().date_from).toBeUndefined();
    expect(lastParams().date_to).toBeUndefined();
    expect(screen.getByLabelText('Du')).toHaveValue('');
    expect(screen.getByLabelText('Au')).toHaveValue('');
  });

  it('offers LLM_CALL in the event filter with its translated label', () => {
    renderPage();
    const option = Array.from(actionSelect().options).find((o) => o.value === 'LLM_CALL');
    expect(option?.textContent).toBe('Appel au LLM');
  });

  it('filters on LLM_CALL', () => {
    mockRows([log({})]);
    renderPage();
    goToPage2();

    fireEvent.change(actionSelect(), { target: { value: 'LLM_CALL' } });

    expect(lastParams()).toMatchObject({ action: 'LLM_CALL', page: 1 });
  });

  it('displays an LLM gateway event with its labels, never its raw keys', () => {
    const details: AuditLog['details'] = {
      llm_call: {
        outcome: 'ANSWERED',
        failed_stage: null,
        error_type: null,
        received: 5,
        retained: 1,
        excluded: { DUPLICATE: 1, LAYER_NOT_ALLOWED: 3 },
        prompt_chars: 120,
        answer_chars: 40,
        tokens_restored: 1,
        tokens_withheld: 0,
        gateway_ms: 12,
      },
    };
    mockRows([log({ action: 'LLM_CALL', resource_type: 'LLM_GATEWAY', result: 'SUCCESS', details })]);
    renderPage();
    const table = screen.getByRole('table');
    expect(table).toHaveTextContent('Appel au LLM');
    expect(table).toHaveTextContent('Passerelle LLM');
    expect(table.textContent).not.toMatch(/auditActions\.|resourceTypes\./);
  });
});
