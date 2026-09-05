import type { ReportItem } from '../../types';

/** Sort a ReportItem list by ``order_index`` ascending, returning a new
 *  array. Safe on ``undefined`` / empty input. The copy means callers
 *  can sort a frozen cache entry without mutating React Query's stored
 *  data.
 *
 *  ReportEditor used to read ``items`` from a local ``buffer`` copy
 *  hydrated once from the query cache — so item mutations on the cache
 *  (create/delete/update/reorder, all already wired with optimistic
 *  updates) never propagated into the items tab. Deriving directly
 *  from the cache via this helper closes the dual-track. */
export function sortedItemsByOrder(items: ReportItem[] | undefined): ReportItem[] {
  if (!items) return [];
  return [...items].sort((a, b) => a.order_index - b.order_index);
}