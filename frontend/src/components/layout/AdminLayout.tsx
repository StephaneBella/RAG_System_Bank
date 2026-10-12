import { Outlet } from 'react-router-dom';

import { Icon } from '@/components/ui/Icon';

import { LanguageSwitcher } from './LanguageSwitcher';
import { Sidebar } from './Sidebar';
import { UserMenu } from './UserMenu';

export function AdminLayout(): JSX.Element {
  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-hairline bg-surface-container-lowest/95 px-6 backdrop-blur">
          <div className="flex items-center gap-2 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-container text-white">
              <Icon name="admin_panel_settings" className="text-[20px]" filled />
            </span>
          </div>
          <div className="flex flex-1 items-center justify-end gap-3">
            <LanguageSwitcher />
            <UserMenu />
          </div>
        </header>
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-6">
          <div className="mx-auto w-full max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
