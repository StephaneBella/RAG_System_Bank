import i18next from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';

import en from './locales/en.json';
import fr from './locales/fr.json';

export const defaultNS = 'translation';

export const supportedLanguages = ['fr', 'en'] as const;
export type SupportedLanguage = (typeof supportedLanguages)[number];

void i18next
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      fr: { translation: fr },
      en: { translation: en },
    },
    fallbackLng: 'fr',
    supportedLngs: supportedLanguages,
    compatibilityJSON: 'v3',
    interpolation: { escapeValue: false },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'rag.language',
      caches: ['localStorage'],
    },
  });

export default i18next;
