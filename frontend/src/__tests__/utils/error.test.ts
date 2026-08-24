import { describe, expect, it } from 'vitest';

import { formatError } from '../../utils/error';

describe('formatError', () => {
  it('returns the server detail string when present (axios shape)', () => {
    const err = { response: { data: { detail: '用户名或密码错误' } } };
    expect(formatError(err, '登录失败')).toBe('用户名或密码错误');
  });

  it('returns the fallback when there is no response object', () => {
    expect(formatError(new Error('network down'), '请求失败')).toBe('请求失败');
  });

  it('returns the fallback when response.data.detail is missing', () => {
    const err = { response: { data: {} } };
    expect(formatError(err, 'default')).toBe('default');
  });

  it('returns the fallback when detail is not a string', () => {
    // Some servers send `{ detail: [{ ... }] }` for 422 validation.
    // We deliberately fall back to the caller string rather than stringify.
    const err = { response: { data: { detail: [{ loc: ['body', 'name'] }] } } };
    expect(formatError(err, '校验失败')).toBe('校验失败');
  });

  it('returns the fallback for null', () => {
    expect(formatError(null, '未知错误')).toBe('未知错误');
  });

  it('returns the fallback for undefined', () => {
    expect(formatError(undefined, '未知错误')).toBe('未知错误');
  });

  it('returns the fallback for primitives', () => {
    expect(formatError('just a string', 'fallback')).toBe('fallback');
    expect(formatError(42, 'fallback')).toBe('fallback');
    expect(formatError(true, 'fallback')).toBe('fallback');
  });

  it('handles nested response that is itself null', () => {
    const err = { response: null };
    expect(formatError(err, 'fallback')).toBe('fallback');
  });
});
