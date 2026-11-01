import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import { Icon } from '@/components/ui/Icon';
import { useAuth } from '@/context/AuthContext';
import { useToast } from '@/components/ui/Toast';
import { cn, initials } from '@/lib/utils';

export function UserMenu({ compact = false }: { compact?: boolean }): JSX.Element {
  const { user, logout } = useAuth();
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    toast.success(t('auth.logoutSuccess'));
    navigate('/login', { replace: true });
  };

  if (!user) return <div />;

  return (
    <div className={cn('flex items-center gap-3', compact && 'gap-2')}>
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-container font-label-md text-label-md font-semibold text-white">
          {initials(user.email)}
        </span>
        {!compact && (
          <div className="hidden flex-col leading-tight sm:flex">
            <span className="font-body-sm text-body-sm font-semibold text-on-surface">
              {user.email}
            </span>
            <span className="font-body-sm text-body-sm text-on-surface-variant">
              {t(`roles.${user.role}`)}
            </span>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={handleLogout}
        title={t('common.logout')}
        className="flex h-9 items-center gap-1.5 rounded-lg border border-hairline px-2.5 text-on-surface-variant transition-colors hover:bg-surface-container"
      >
        <Icon name="logout" className="text-[18px]" />
        <span className="hidden font-label-md text-label-md md:inline">{t('common.logout')}</span>
      </button>
    </div>
  );
}
