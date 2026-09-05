/** Pure-function test for ``sortedItemsByOrder``.
 *
 *  The helper is the single source of truth for ReportEditor's items
 *  list — it derives from the React Query cache (server truth) and the
 *  previous bug (TODO-4) was that the page rendered a stale local copy.
 *  Locking the sort behaviour down here keeps the contract simple.
 */

import { describe, expect, it } from 'vitest';

import { sortedItemsByOrder } from '../../pages/ReportEditor/itemsView';
import type { ReportItem } from '../../types';

function makeItem(id: number, order_index: number): ReportItem {
  return {
    id,
    report_id: 9,
    name: `item ${id}`,
    item_type: 'table',
    order_index,
    table_name: 'foo',
    fields: [],
    where_conditions: [],
    group_by: [],
    order_by: [],
    display_config: {},
    limit: 100,
  };
}

describe('sortedItemsByOrder', () => {
  it('returns [] for undefined', () => {
    expect(sortedItemsByOrder(undefined)).toEqual([]);
  });

  it('returns [] for an empty array', () => {
    expect(sortedItemsByOrder([])).toEqual([]);
  });

  it('sorts by order_index ascending', () => {
    const a = makeItem(1, 0);
    const b = makeItem(2, 1);
    const c = makeItem(3, 2);
    // Hand the helper an intentionally unsorted input.
    expect(sortedItemsByOrder([c, a, b])).toEqual([a, b, c]);
  });

  it('does not mutate the input array', () => {
    const a = makeItem(1, 0);
    const b = makeItem(2, 1);
    const input = [b, a];
    const before = [...input];
    sortedItemsByOrder(input);
    expect(input).toEqual(before);
  });

  it('returns a new array reference (callers can mutate without poisoning the cache)', () => {
    const input = [makeItem(1, 0), makeItem(2, 1)];
    const output = sortedItemsByOrder(input);
    expect(output).not.toBe(input);
  });
});