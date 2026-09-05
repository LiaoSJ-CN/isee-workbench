import { useEffect } from 'react';

/**
 * Bind a global ``window`` keydown handler.
 *
 * By default the handler fires only when (Cmd OR Ctrl) AND the
 * target key are pressed simultaneously — matching the ⌘K / Ctrl+K
 * convention used by every palette the user already knows (Linear,
 * Spotlight, VS Code). ``preventDefault`` is always called so the
 * browser's location-bar ⌘L shortcut doesn't leak through.
 *
 * Pass ``{ requireModifier: false }`` for keys like ``Escape`` that
 * have no modifier convention.
 *
 * The listener is bound for the lifetime of the component; it cleans
 * up on unmount. Multiple calls with the same target are allowed —
 * the latest handler wins on each press (React re-binds on dep
 * change).
 *
 * Usage:
 * ```tsx
 * useGlobalShortcut('k', () => inputRef.current?.focus());
 * useGlobalShortcut('Escape', () => setOpen(false), { requireModifier: false });
 * ```
 */
export function useGlobalShortcut(
  key: string,
  handler: () => void,
  options?: { requireModifier?: boolean },
): void {
  const requireModifier = options?.requireModifier ?? true;
  useEffect(() => {
    const target = key.toLowerCase();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== target) return;
      const hasModifier = e.metaKey || e.ctrlKey;
      if (requireModifier && !hasModifier) return;
      if (!requireModifier && hasModifier) return;
      e.preventDefault();
      handler();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [key, handler, requireModifier]);
}