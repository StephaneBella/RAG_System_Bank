import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { useAuth, landingPathFor } from '@/context/AuthContext';

export function ForbiddenPage(): JSX.Element {
  const { t } = useTranslation();
  const { user } = useAuth();
  const home = user ? landingPathFor(user) : '/login';

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-lg rounded-xl border border-hairline bg-surface-container-lowest shadow-card">
        <EmptyState
          icon="gpp_maybe"
          title={t('errors.forbiddenTitle')}
          description={t('errors.forbiddenBody')}
          action={
            <Link to={home}>
              <Button variant="primary" icon="home">
                {t('errors.goHome')}
              </Button>
            </Link>
          }
        />
      </div>
    </div>
  );
}
