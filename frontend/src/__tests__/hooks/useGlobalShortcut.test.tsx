import { describe, expect, it, vi } from 'vitest';
import { fireEvent, renderHook } from '@testing-library/react';

import { useGlobalShortcut } from '../../hooks/useGlobalShortcut';

describe('useGlobalShortcut', () => {
  it('fires the handler on Cmd+K (macOS)', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('k', handler));

    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('fires the handler on Ctrl+K (Windows / Linux)', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('k', handler));

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does NOT fire on a bare K (no modifier)', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('k', handler));

    fireEvent.keyDown(window, { key: 'k' });
    expect(handler).not.toHaveBeenCalled();
  });

  it('does NOT fire on a different shortcut (Cmd+S)', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('k', handler));

    fireEvent.keyDown(window, { key: 's', metaKey: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it('calls preventDefault on the event when it fires', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('k', handler));

    const event = new KeyboardEvent('keydown', {
      key: 'k',
      metaKey: true,
      cancelable: true,
    });
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault');
    window.dispatchEvent(event);
    expect(preventDefaultSpy).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('cleans up the listener on unmount', () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useGlobalShortcut('k', handler));
    unmount();

    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it('matches case-insensitively (Cmd+K vs Cmd+k)', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('k', handler));

    fireEvent.keyDown(window, { key: 'K', metaKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('fires without a modifier when requireModifier is false (Escape)', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('Escape', handler, { requireModifier: false }));

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does NOT fire with a modifier when requireModifier is false', () => {
    const handler = vi.fn();
    renderHook(() => useGlobalShortcut('Escape', handler, { requireModifier: false }));

    fireEvent.keyDown(window, { key: 'Escape', metaKey: true });
    expect(handler).not.toHaveBeenCalled();
  });
});