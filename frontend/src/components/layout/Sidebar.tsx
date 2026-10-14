import { NavLink } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Icon } from '@/components/ui/Icon';
import { useAuth } from '@/context/AuthContext';
import { cn, initials } from '@/lib/utils';

const navItems = [
  { to: '/admin/documents', icon: 'description', labelKey: 'nav.documents' },
  { to: '/admin/users', icon: 'group', labelKey: 'nav.users' },
  { to: '/admin/audit', icon: 'history', labelKey: 'nav.audit' },
] as const;

export function Sidebar(): JSX.Element {
  const { t } = useTranslation();
  const { user } = useAuth();

  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-hairline bg-surface-container-lowest lg:flex">
      <div className="flex min-h-[4.5rem] items-center gap-3 border-b border-hairline px-5 py-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-container text-white">
          <Icon name="admin_panel_settings" className="text-[20px]" filled />
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate font-headline-sm text-headline-sm font-semibold leading-snug text-primary">
            {t('common.libraryName')}
          </span>
          <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
            {t('nav.adminPanel')}
          </span>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-3">
        <span className="px-3 pb-1 pt-2 font-label-sm text-label-sm uppercase tracking-wider text-faint">
          {t('nav.adminConsole')}
        </span>
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-lg px-3 py-2.5 font-label-md text-label-md transition-colors',
                isActive
                  ? 'bg-primary-container text-white shadow-sm'
                  : 'text-on-surface-variant hover:bg-surface-container',
              )
            }
          >
            <Icon name={item.icon} className="text-[20px]" />
            {t(item.labelKey)}
          </NavLink>
        ))}
      </nav>

      <div className="flex items-center gap-2.5 border-t border-hairline p-4">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-container font-label-md text-label-md font-semibold text-primary">
          {initials(user?.email)}
        </span>
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate font-body-sm text-body-sm font-semibold text-on-surface">
            {user?.email}
          </span>
          <span className="font-body-sm text-body-sm text-on-surface-variant">
            {user ? t(`roles.${user.role}`) : ''}
          </span>
        </div>
      </div>
    </aside>
  );
}
