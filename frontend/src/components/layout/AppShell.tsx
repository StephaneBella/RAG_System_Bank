import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Icon } from '@/components/ui/Icon';
import { useAuth } from '@/context/AuthContext';

import { LanguageSwitcher } from './LanguageSwitcher';
import { UserMenu } from './UserMenu';

export function AppShell({ children }: { children: ReactNode }): JSX.Element {
  const { t } = useTranslation();
  const { user, isAdmin } = useAuth();

  return (
    <div className="flex min-h-screen flex-col bg-slate-50">
      <header className="sticky top-0 z-30 border-b border-hairline bg-surface-container-lowest/95 backdrop-blur">
        <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-6">
          <Link to="/library" className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-container text-primary">
              <Icon name="lock" className="text-[20px]" filled />
            </span>
            <div className="hidden flex-col leading-tight sm:flex">
              <span className="font-headline-sm text-headline-sm font-semibold text-primary">
                {t('common.libraryName')}
              </span>
              <span className="font-body-sm text-body-sm text-on-surface-variant">
                {user?.department_name ?? ''}
              </span>
            </div>
          </Link>
          <div className="flex items-center gap-3">
            {isAdmin && (
              <Link
                to="/admin/documents"
                className="hidden items-center gap-1.5 rounded-lg bg-surface-container px-3 py-2 font-label-md text-label-md text-primary transition-colors hover:bg-surface-container-high sm:flex"
              >
                <Icon name="admin_panel_settings" className="text-[18px]" />
                {t('nav.adminPanel')}
              </Link>
            )}
            <LanguageSwitcher />
            <UserMenu />
          </div>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="border-t border-hairline bg-surface-container-low py-5">
        <div className="mx-auto flex w-full max-w-5xl flex-col items-center justify-between gap-2 px-6 text-on-surface-variant sm:flex-row">
          <span className="font-body-sm text-body-sm">© 2025 {t('common.libraryName')}</span>
          <span className="font-body-sm text-body-sm">{t('common.secureConnection')}</span>
        </div>
      </footer>
    </div>
  );
}
