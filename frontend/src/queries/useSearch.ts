import { useQuery } from '@tanstack/react-query';

import { searchApi } from '../api';
import type { SearchResponse } from '../types';
import { queryKeys } from './keys';

/**
 * `useSearch` — top-bar command palette backing query.
 *
 * Returns three grouped result lists (reports / dashboards / data
 * sources), each independently capped server-side by ``limitPerKind``.
 *
 * Defaults chosen to match the backend's ``Query(default=8, ge=1,
 * le=50)`` so the palette and the server stay symmetric.
 *
 * - ``enabled: q.trim().length > 0`` keeps the request cold while the
 *   input is empty (avoids a flash of empty-state on every focus).
 * - ``staleTime: 30_000`` lets repeated identical queries within 30 s
 *   hit the cache (e.g. user re-focuses the palette and retypes the
 *   same fragment).
 * - ``retry: false`` so 4xx / 5xx surfaces immediately — the palette
 *   shows the error inline rather than retry-looping.
 */
export function useSearch(q: string, limitPerKind = 8) {
  return useQuery<SearchResponse>({
    queryKey: queryKeys.search.results(q, limitPerKind),
    queryFn: () => searchApi.search(q, limitPerKind),
    enabled: q.trim().length > 0,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: false,
  });
}

/**
 * Pure relevance scorer — used by the palette to order hits within
 * each group. Lower is better:
 *
 * - ``0`` exact match (case-insensitive).
 * - ``1`` starts with the query.
 * - ``2`` contains the query.
 * - ``99`` no match (defensive — palette should already have filtered
 *   on this server-side).
 *
 * Ties are broken by ``name.length`` ascending so the shortest
 * plausible match surfaces first (a query of "财务" prefers "财务" over
 * "财务报表聚合查询-财务部门_2025Q1副本").
 */
export function scoreRef(name: string, q: string): number {
  const n = name.toLowerCase();
  const k = q.toLowerCase();
  if (n === k) return 0;
  if (n.startsWith(k)) return 1;
  if (n.includes(k)) return 2;
  return 99;
}

/**
 * Composite sort: primary key is the relevance tier (exact > prefix >
 * contains); ties broken by ascending name length so the most focused
 * match surfaces first. Stable: equal-key rows keep their input order.
 */
export function sortByRelevance<T extends { name: string }>(
  items: readonly T[],
  q: string,
): T[] {
  return [...items].sort((a, b) => {
    const sa = scoreRef(a.name, q);
    const sb = scoreRef(b.name, q);
    if (sa !== sb) return sa - sb;
    if (a.name.length !== b.name.length) return a.name.length - b.name.length;
    return 0;
  });
}