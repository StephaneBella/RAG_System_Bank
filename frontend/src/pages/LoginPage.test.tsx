import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AuthProvider } from '@/context/AuthContext';
import { ToastProvider } from '@/components/ui/Toast';

import { LoginPage } from './LoginPage';

function renderLogin() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={['/login']}>
            <LoginPage />
          </MemoryRouter>
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe('LoginPage', () => {
  it('renders the credential fields', () => {
    renderLogin();
    expect(screen.getByLabelText(/Identifiant|Banking ID/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText(/Mot de passe d'habilitation|Authorisation password/i),
    ).toBeInTheDocument();
  });

  it('blocks submission and flags empty fields', () => {
    renderLogin();
    const submit = screen.getAllByRole('button').find((b) => b.getAttribute('type') === 'submit');
    expect(submit).toBeDefined();
    fireEvent.click(submit as HTMLButtonElement);

    expect(screen.getByLabelText(/Identifiant|Banking ID/i)).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    expect(
      screen.getByLabelText(/Mot de passe d'habilitation|Authorisation password/i),
    ).toHaveAttribute('aria-invalid', 'true');
  });
});
