import type { Config } from 'tailwindcss';

/**
 * Design tokens ported from the Stitch export (`DESIGN.md` and the
 * `tailwind.config` embedded in the exported HTML screens).
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Material 3 surface / brand tokens
        background: '#faf8ff',
        surface: '#faf8ff',
        'surface-dim': '#d2d9f4',
        'surface-bright': '#faf8ff',
        'surface-variant': '#dae2fd',
        'surface-container-lowest': '#ffffff',
        'surface-container-low': '#f2f3ff',
        'surface-container': '#eaedff',
        'surface-container-high': '#e2e7ff',
        'surface-container-highest': '#dae2fd',
        'on-surface': '#131b2e',
        'on-surface-variant': '#444651',
        'inverse-surface': '#283044',
        'inverse-on-surface': '#eef0ff',
        outline: '#757682',
        'outline-variant': '#c5c5d3',
        'surface-tint': '#4059aa',
        primary: '#00236f',
        'on-primary': '#ffffff',
        'primary-container': '#1e3a8a',
        'on-primary-container': '#90a8ff',
        'inverse-primary': '#b6c4ff',
        secondary: '#006a61',
        'on-secondary': '#ffffff',
        'secondary-container': '#86f2e4',
        'on-secondary-container': '#006f66',
        tertiary: '#003120',
        'on-tertiary': '#ffffff',
        'tertiary-container': '#004a32',
        'on-tertiary-container': '#4ac08f',
        error: '#ba1a1a',
        'on-error': '#ffffff',
        'error-container': '#ffdad6',
        'on-error-container': '#93000a',

        // Functional accent palette from DESIGN.md
        navy: '#1e3a8a',
        'navy-deep': '#172554',
        accent: '#3b82f6',
        teal: '#0d9488',
        emerald: '#059669',
        slate: {
          50: '#f8fafc',
          100: '#f1f5f9',
          400: '#94a3b8',
          600: '#475569',
          900: '#0f172a',
        },
        ink: '#0f172a',
        muted: '#475569',
        faint: '#94a3b8',
        hairline: '#e2e8f0',

        // Classification tokens
        classification: {
          secret: '#991b1b',
          'secret-bg': '#fef2f2',
          'secret-border': '#fca5a5',
          confidential: '#b45309',
          'confidential-bg': '#fffbeb',
          'confidential-border': '#fcd34d',
          internal: '#1e40af',
          'internal-bg': '#eff6ff',
          'internal-border': '#bfdbfe',
          public: '#334155',
          'public-bg': '#f1f5f9',
          'public-border': '#cbd5e1',
        },
      },
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        display: ['30px', { lineHeight: '38px', letterSpacing: '-0.025em' }],
        'headline-lg': ['23px', { lineHeight: '30px', letterSpacing: '-0.02em' }],
        'headline-md': ['19px', { lineHeight: '26px', letterSpacing: '-0.015em' }],
        'headline-sm': ['16px', { lineHeight: '22px', letterSpacing: '-0.01em' }],
        'body-lg': ['16px', { lineHeight: '24px', letterSpacing: '-0.005em' }],
        'body-table': ['15px', { lineHeight: '23px' }],
        'body-md': ['14px', { lineHeight: '21px' }],
        'body-sm': ['13px', { lineHeight: '19px' }],
        'label-md': ['12px', { lineHeight: '16px', letterSpacing: '0.01em' }],
        'label-sm': ['9px', { lineHeight: '12px', letterSpacing: '0.02em' }],
        'code-mono': ['12px', { lineHeight: '16px' }],
        badge: ['12px', { lineHeight: '16px', letterSpacing: '0.02em' }],
      },
      borderRadius: {
        sm: '0.25rem',
        DEFAULT: '0.25rem',
        md: '0.375rem',
        lg: '0.5rem',
        xl: '0.75rem',
        '2xl': '1rem',
        '3xl': '1.5rem',
        full: '9999px',
      },
      spacing: {
        gutter: '1.5rem',
        'gutter-dense': '0.75rem',
        margin: '2rem',
        'margin-compact': '1.5rem',
        'space-xs': '0.25rem',
        'space-sm': '0.5rem',
        'space-md': '0.75rem',
        'space-lg': '1.25rem',
        'space-xl': '2rem',
      },
      boxShadow: {
        card: '0 1px 3px 0 rgba(15, 23, 42, 0.04), 0 1px 2px -1px rgba(15, 23, 42, 0.02)',
        'card-hover':
          '0 4px 6px -1px rgba(15, 23, 42, 0.06), 0 2px 4px -2px rgba(15, 23, 42, 0.04)',
        modal:
          '0 20px 25px -5px rgba(15, 23, 42, 0.08), 0 8px 10px -6px rgba(15, 23, 42, 0.04)',
        focus: '0 0 0 2px #ffffff, 0 0 0 4px #3b82f6',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 150ms ease-out',
        'slide-up': 'slide-up 180ms ease-out',
      },
    },
  },
  plugins: [],
} satisfies Config;
