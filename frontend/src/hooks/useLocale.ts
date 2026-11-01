import { useTranslation } from 'react-i18next';

export function useLocale(): string {
  const { i18n } = useTranslation();
  return (i18n.resolvedLanguage ?? 'fr').startsWith('en') ? 'en-US' : 'fr-FR';
}
