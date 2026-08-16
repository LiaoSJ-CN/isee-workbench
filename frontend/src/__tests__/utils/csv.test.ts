import { describe, expect, it } from 'vitest'

import { csvEscape } from '../../utils/csv'

describe('csvEscape (RFC 4180)', () => {
  it('returns plain strings unchanged', () => {
    expect(csvEscape('hello')).toBe('hello')
    expect(csvEscape('region-east')).toBe('region-east')
    expect(csvEscape('2024-01-15')).toBe('2024-01-15')
    expect(csvEscape('')).toBe('')
  })

  it('quotes fields containing the delimiter (comma)', () => {
    expect(csvEscape('a,b')).toBe('"a,b"')
  })

  it('quotes fields containing double quotes and doubles them', () => {
    expect(csvEscape('say "hi"')).toBe('"say ""hi"""')
  })

  it('quotes fields containing CR or LF', () => {
    expect(csvEscape('line1\nline2')).toBe('"line1\nline2"')
    expect(csvEscape('a\rb')).toBe('"a\rb"')
    expect(csvEscape('a\r\nb')).toBe('"a\r\nb"')
  })

  it('quotes and escapes when multiple special chars are present', () => {
    expect(csvEscape('a,"b"\nc')).toBe('"a,""b""\nc"')
  })

  it('does not quote single quotes (they are not special in CSV)', () => {
    // Single quotes only matter in SQL/JS string literals; RFC 4180 CSV
    // only requires escaping the double-quote character itself.
    expect(csvEscape("it's fine")).toBe("it's fine")
  })

  it('does not quote semicolons or other punctuation', () => {
    expect(csvEscape('a;b:c|d')).toBe('a;b:c|d')
  })

  it('handles unicode without modification', () => {
    expect(csvEscape('你好')).toBe('你好')
    expect(csvEscape('日本語')).toBe('日本語')
  })
})