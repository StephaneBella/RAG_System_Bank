import { describe, expect, it } from 'vitest';

import { cn, fileExtension, formatFileSize, initials, isTokenExpired, truncateHash } from './utils';

describe('cn', () => {
  it('keeps custom font-size tokens alongside text colors', () => {
    expect(cn('text-white', 'text-label-md')).toContain('text-white');
    expect(cn('text-white', 'text-label-md')).toContain('text-label-md');
    expect(cn('text-label-sm', 'text-on-surface-variant')).toContain('text-label-sm');
    expect(cn('text-body-sm', 'text-on-surface')).toContain('text-body-sm');
  });
});

describe('formatFileSize', () => {
  it('formats bytes into human readable units', () => {
    expect(formatFileSize(0)).toBe('—');
    expect(formatFileSize(512)).toBe('512 o');
    expect(formatFileSize(2048)).toBe('2.0 Ko');
    expect(formatFileSize(1024 * 1024 * 3)).toBe('3.0 Mo');
  });
});

describe('fileExtension', () => {
  it('extracts and uppercases the extension', () => {
    expect(fileExtension('rapport.pdf')).toBe('PDF');
    expect(fileExtension('archive.tar.gz')).toBe('GZ');
    expect(fileExtension('no-extension')).toBe('');
    expect(fileExtension(undefined)).toBe('');
  });
});

describe('initials', () => {
  it('builds initials from names and emails', () => {
    expect(initials('Sophie Moreau')).toBe('SM');
    expect(initials('alexandre.laurent@banque-institution.fr')).toBe('AL');
    expect(initials(null)).toBe('?');
  });
});

describe('truncateHash', () => {
  it('truncates long hashes', () => {
    expect(truncateHash('abcdef', 4)).toBe('abcd…');
    expect(truncateHash('abc', 4)).toBe('abc');
    expect(truncateHash(null)).toBe('—');
  });
});

describe('isTokenExpired', () => {
  const encode = (payload: object) =>
    `x.${btoa(JSON.stringify(payload)).replace(/=+$/, '')}.y`;

  it('detects expired and valid tokens', () => {
    const past = Math.floor(Date.now() / 1000) - 60;
    const future = Math.floor(Date.now() / 1000) + 600;
    expect(isTokenExpired(encode({ exp: past }))).toBe(true);
    expect(isTokenExpired(encode({ exp: future }))).toBe(false);
    expect(isTokenExpired('not-a-token')).toBe(false);
  });
});
