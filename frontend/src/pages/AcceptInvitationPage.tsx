import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { acceptInvitation } from '@/api/auth.api';
import { getApiErrorMessage, getErrorStatus } from '@/api/errors';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, PasswordInput } from '@/components/ui/Input';
import { useToast } from '@/components/ui/Toast';
import { LanguageSwitcher } from '@/components/layout/LanguageSwitcher';

const MIN_PASSWORD_LENGTH = 12;

export function AcceptInvitationPage(): JSX.Element {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams] = useSearchParams();

  const [token, setToken] = useState(searchParams.get('token') ?? '');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});

    const nextErrors: Record<string, string> = {};
    if (!token.trim()) nextErrors.token = t('validation.required');
    if (password.length < MIN_PASSWORD_LENGTH) {
      nextErrors.password = t('validation.passwordTooShort');
    }
    if (password !== confirmPassword) {
      nextErrors.confirmPassword = t('validation.passwordMismatch');
    }
    if (Object.keys(nextErrors).length > 0) {
      setFieldErrors(nextErrors);
      return;
    }

    setSubmitting(true);
    try {
      await acceptInvitation({ token: token.trim(), password });
      toast.success(t('invitation.success'));
      navigate('/login', { replace: true });
    } catch (error) {
      const status = getErrorStatus(error);
      if (status === 400 || status === 404) {
        setFormError(t('invitation.invalidToken'));
      } else {
        setFormError(getApiErrorMessage(error) ?? t('errors.generic'));
      }
    } finally {
      setSubmitting(false);
    }
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
            <Icon name="mark_email_read" className="text-[28px]" filled />
          </div>
          <h1 className="font-headline-md text-headline-md font-semibold tracking-tight text-on-surface">
            {t('invitation.title')}
          </h1>
          <p className="mt-1.5 font-body-sm text-body-sm text-on-surface-variant">
            {t('invitation.subtitle')}
          </p>
        </div>

        <form className="flex flex-col gap-5 px-8 pb-8" onSubmit={handleSubmit} noValidate>
          <Input
            id="invitation-token"
            leadingIcon="vpn_key"
            label={t('invitation.tokenLabel')}
            placeholder={t('invitation.tokenPlaceholder')}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            error={fieldErrors.token}
          />

          <PasswordInput
            id="invitation-password"
            autoComplete="new-password"
            leadingIcon="key"
            label={t('invitation.passwordLabel')}
            placeholder={t('invitation.passwordPlaceholder')}
            showLabel={t('auth.showPassword')}
            hideLabel={t('auth.hidePassword')}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            error={fieldErrors.password}
          />

          <PasswordInput
            id="invitation-password-confirm"
            autoComplete="new-password"
            leadingIcon="key"
            label={t('invitation.confirmPasswordLabel')}
            placeholder={t('invitation.confirmPasswordPlaceholder')}
            showLabel={t('auth.showPassword')}
            hideLabel={t('auth.hidePassword')}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            error={fieldErrors.confirmPassword}
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
            {submitting ? t('invitation.submitting') : t('invitation.submit')}
          </Button>

          <Link
            to="/login"
            className="text-center font-body-sm text-body-sm text-primary hover:underline"
          >
            {t('invitation.goToLogin')}
          </Link>
        </form>
      </div>
    </div>
  );
}
