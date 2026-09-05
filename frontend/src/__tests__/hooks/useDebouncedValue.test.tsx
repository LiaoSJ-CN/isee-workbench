import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { useDebouncedValue } from '../../hooks/useDebouncedValue';

describe('useDebouncedValue', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns the initial value synchronously', () => {
    const { result } = renderHook(() => useDebouncedValue('initial', 250));
    expect(result.current).toBe('initial');
  });

  it('returns the last value if it changes multiple times within the window', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useDebouncedValue(value, 250),
      { initialProps: { value: 'a' } },
    );

    rerender({ value: 'b' });
    rerender({ value: 'c' });
    rerender({ value: 'd' });
    // Still the initial value — the debounce timer hasn't fired.
    expect(result.current).toBe('a');

    // Advance just enough for the timer to fire.
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(result.current).toBe('d');
  });

  it('coalesces rapid changes into one update', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: number }) => useDebouncedValue(value, 250),
      { initialProps: { value: 0 } },
    );

    rerender({ value: 1 });
    act(() => vi.advanceTimersByTime(100));
    rerender({ value: 2 });
    act(() => vi.advanceTimersByTime(100));
    rerender({ value: 3 });
    // Total elapsed = 200ms < 250ms — no update yet.
    expect(result.current).toBe(0);

    act(() => vi.advanceTimersByTime(250));
    expect(result.current).toBe(3);
  });
});