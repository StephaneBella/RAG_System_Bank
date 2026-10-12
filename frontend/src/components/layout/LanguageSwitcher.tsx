import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';
import { supportedLanguages, type SupportedLanguage } from '@/i18n';

export function LanguageSwitcher({ className }: { className?: string }): JSX.Element {
  const { i18n } = useTranslation();
  const current = (i18n.resolvedLanguage ?? 'fr').slice(0, 2) as SupportedLanguage;

  return (
    <div
      className={cn(
        'inline-flex items-center rounded-lg border border-hairline bg-slate-100 p-0.5',
        className,
      )}
      role="group"
      aria-label="Language"
    >
      {supportedLanguages.map((language) => (
        <button
          key={language}
          type="button"
          onClick={() => void i18n.changeLanguage(language)}
          className={cn(
            'rounded-md px-2 py-0.5 font-label-sm text-label-sm uppercase transition-colors',
            language === current
              ? 'bg-surface-container-lowest font-semibold text-ink shadow-[0_1px_2px_rgba(0,0,0,0.05)]'
              : 'text-muted hover:bg-hairline',
          )}
        >
          {language}
        </button>
      ))}
    </div>
  );
}
