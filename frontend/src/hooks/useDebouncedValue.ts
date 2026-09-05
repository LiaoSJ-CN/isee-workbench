import { useEffect, useState } from 'react';

/**
 * Returns ``value`` after it has stayed unchanged for ``ms`` ms.
 *
 * Pattern copied from the inline ``setTimeout`` / ``clearTimeout``
 * debounce already used in :component:`DashboardGridEditor` so we
 * don't pull in a debounce package for a single consumer.
 *
 * Usage:
 * ```tsx
 * const debouncedQuery = useDebouncedValue(query, 250);
 * useSearch(debouncedQuery);  // only fires once per "settled" burst
 * ```
 */
export function useDebouncedValue<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}