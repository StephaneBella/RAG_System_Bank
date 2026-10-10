import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { getApiErrorMessage, getErrorStatus, getValidationIssues } from '@/api/errors';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, PasswordInput } from '@/components/ui/Input';
import { LanguageSwitcher } from '@/components/layout/LanguageSwitcher';
import { landingPathFor, useAuth } from '@/context/AuthContext';

interface LocationState {
  from?: { pathname?: string };
}

const DEMO_ACCOUNTS = {
  admin: 'sophie.moreau@banque-institution.fr',
  employee: 'alexandre.laurent@banque-institution.fr',
} as const;

export function LoginPage(): JSX.Element {
  const { t } = useTranslation();
  const { login, isAuthenticated, sessionExpired, clearSessionExpired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as LocationState | null;

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    return () => clearSessionExpired();
  }, [clearSessionExpired]);

  if (isAuthenticated) {
    return <Navigate to={state?.from?.pathname ?? '/library'} replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});

    const nextErrors: Record<string, string> = {};
    if (!email) nextErrors.email = t('validation.emailRequired');
    if (!password) nextErrors.password = t('validation.passwordRequired');
    if (Object.keys(nextErrors).length > 0) {
      setFieldErrors(nextErrors);
      return;
    }

    setSubmitting(true);
    try {
      const user = await login(email, password);
      navigate(state?.from?.pathname ?? landingPathFor(user), { replace: true });
    } catch (error) {
      const status = getErrorStatus(error);
      if (status === 422) {
        const issues = getValidationIssues(error);
        const mapped: Record<string, string> = {};
        issues.forEach((issue) => {
          const field = String(issue.loc[issue.loc.length - 1]);
          mapped[field] = issue.msg;
        });
        setFieldErrors(mapped);
      } else if (status === 401) {
        setFormError(t('auth.invalidCredentials'));
      } else {
        setFormError(getApiErrorMessage(error) ?? t('errors.generic'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const fillDemo = (value: string) => {
    setEmail(value);
    setPassword('Password123');
    setFormError(null);
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-4 py-10">
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center opacity-40">
        <svg
          className="h-[960px] w-[960px] text-surface-container-high"
          fill="none"
          stroke="currentColor"
          strokeWidth="0.75"
          viewBox="0 0 800 800"
        >
          <circle cx="400" cy="400" r="380" strokeDasharray="3 6" />
          <circle cx="400" cy="400" r="320" />
          <circle cx="400" cy="400" r="260" strokeDasharray="4 4" />
          <circle cx="400" cy="400" r="200" />
          <circle cx="400" cy="400" r="140" strokeDasharray="2 8" />
          <polygon opacity="0.35" points="400,60 740,400 400,740 60,400" />
          <polygon opacity="0.25" points="400,120 680,400 400,680 120,400" />
          <line strokeDasharray="2 10" x1="0" x2="800" y1="400" y2="400" />
          <line strokeDasharray="2 10" x1="400" x2="400" y1="0" y2="800" />
        </svg>
      </div>

      <div className="absolute right-4 top-4 z-10">
        <LanguageSwitcher />
      </div>

      <div className="relative w-full max-w-[512px] rounded-xl border border-hairline bg-surface-container-lowest shadow-xl shadow-primary/5">
        <div className="flex flex-col items-center p-8 pb-6 text-center">
          <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-xl bg-surface-container text-primary shadow-sm">
            <Icon name="lock" className="text-[28px]" filled />
          </div>
          <h1 className="font-headline-md text-headline-md font-semibold tracking-tight text-on-surface">
            {t('auth.brand')}
          </h1>
          <p className="mt-1.5 font-body-sm text-body-sm text-on-surface-variant">
            {t('auth.subtitle')}
          </p>
        </div>

        {sessionExpired && (
          <div className="mx-8 mb-4 flex items-center gap-2 rounded-lg border border-[#fcd34d] bg-[#fffbeb] px-3 py-2.5 font-body-sm text-body-sm text-[#b45309]">
            <Icon name="timer" className="text-[18px]" />
            {t('auth.sessionExpired')}
          </div>
        )}

        <form className="flex flex-col gap-5 px-8 pb-8" onSubmit={handleSubmit} noValidate>
          <Input
            id="login-email"
            type="email"
            autoComplete="username"
            leadingIcon="badge"
            label={t('auth.emailLabel')}
            placeholder={t('auth.emailPlaceholder')}
            trailingLabel={
              <span className="font-code-mono text-code-mono text-outline">
                {t('auth.emailBadge')}
              </span>
            }
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            error={fieldErrors.email}
          />

          <PasswordInput
            id="login-password"
            autoComplete="current-password"
            leadingIcon="key"
            label={t('auth.passwordLabel')}
            placeholder={t('auth.passwordPlaceholder')}
            showLabel={t('auth.showPassword')}
            hideLabel={t('auth.hidePassword')}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            error={fieldErrors.password}
            trailingLabel={
              <span className="font-label-sm text-label-sm text-primary">
                {t('auth.forgot')}
              </span>
            }
          />

          {formError && (
            <div className="flex items-center gap-2 rounded-lg border border-[#fca5a5] bg-[#fef2f2] px-3 py-2.5 font-body-sm text-body-sm text-[#991b1b]">
              <Icon name="error" className="text-[18px]" />
              {formError}
            </div>
          )}

          <Button
            type="submit"
            size="lg"
            fullWidth
            isLoading={submitting}
            className="bg-primary-container"
          >
            {submitting ? t('auth.signingIn') : t('auth.signIn')}
          </Button>

          <div className="rounded-lg border border-hairline bg-slate-50 p-3">
            <div className="mb-2 flex items-center gap-1.5 font-label-sm text-label-sm uppercase tracking-wider text-faint">
              <Icon name="key" className="text-[16px]" />
              {t('auth.demoHint')}
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => fillDemo(DEMO_ACCOUNTS.admin)}
                className="rounded-md border border-hairline bg-surface-container-lowest px-2.5 py-1 font-body-sm text-body-sm text-on-surface transition-colors hover:bg-surface-container"
              >
                {t('auth.demoAdmin')}
              </button>
              <button
                type="button"
                onClick={() => fillDemo(DEMO_ACCOUNTS.employee)}
                className="rounded-md border border-hairline bg-surface-container-lowest px-2.5 py-1 font-body-sm text-body-sm text-on-surface transition-colors hover:bg-surface-container"
              >
                {t('auth.demoEmployee')}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
