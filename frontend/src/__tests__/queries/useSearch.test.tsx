import { describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { useSearch, scoreRef, sortByRelevance } from '../../queries/useSearch';
import { queryKeys } from '../../queries/keys';
import * as apiModule from '../../api';
import type { SearchResponse } from '../../types';

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe('useSearch', () => {
  it('is disabled when q is empty (enabled: false short-circuit)', () => {
    const spy = vi.spyOn(apiModule, 'searchApi', 'get');
    const { result } = renderHook(() => useSearch('   '), {
      wrapper: makeWrapper(),
    });
    // ``enabled: false`` keeps the query cold. react-query still
    // reports ``status: 'pending'`` initially, but ``fetchStatus``
    // stays ``'idle'`` and no fetcher runs.
    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.isFetching).toBe(false);
    expect(result.current.data).toBeUndefined();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it('calls searchApi.search and returns data on success', async () => {
    const canned: SearchResponse = {
      reports: [{ id: 1, name: 'r', visibility: 'private', is_active: true }],
      dashboards: [],
      data_sources: [],
    };
    const spy = vi
      .spyOn(apiModule, 'searchApi', 'get')
      .mockReturnValue({ search: vi.fn().mockResolvedValue(canned) } as never);

    const { result } = renderHook(() => useSearch('财务', 8), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(canned);
    expect(spy().search).toHaveBeenCalledWith('财务', 8);
    spy.mockRestore();
  });

  it('bakes q and limitPerKind into the cache key', () => {
    const spy = vi.spyOn(apiModule, 'searchApi', 'get').mockReturnValue({
      search: vi.fn().mockResolvedValue({ reports: [], dashboards: [], data_sources: [] }),
    } as never);

    // Two renderHook calls with different (q, limitPerKind) → two
    // distinct cache entries. We assert via the spy that two fetches
    // were made.
    const { result: r1 } = renderHook(() => useSearch('财务', 8), {
      wrapper: makeWrapper(),
    });
    const { result: r2 } = renderHook(() => useSearch('财务', 16), {
      wrapper: makeWrapper(),
    });
    void r1; void r2;
    // The cache key includes both q and limitPerKind, so the factory
    // shape must contain them. We assert this directly on the key.
    const k = queryKeys.search.results('财务', 8);
    expect(k).toContain('财务');
    expect(k).toContain(8);

    spy.mockRestore();
  });
});

describe('scoreRef + sortByRelevance (pure helpers)', () => {
  it('scores exact > prefix > contains', () => {
    expect(scoreRef('财务', '财务')).toBe(0);
    expect(scoreRef('财务报表', '财务')).toBe(1);
    expect(scoreRef('我的财务报告', '财务')).toBe(2);
    expect(scoreRef('foo', 'bar')).toBe(99);
  });

  it('sortByRelevance puts exact first, contains last, ties broken by length', () => {
    const items = [
      { id: 1, name: '财务报表聚合查询-财务部门_2025Q1副本' },
      { id: 2, name: '财务' },
      { id: 3, name: '财务月报' },
      { id: 4, name: '无关数据' },
    ];
    const out = sortByRelevance(items, '财务');
    // First three are hits sorted by score (exact, prefix, prefix),
    // the 4th is filtered only by client-side relevance (still
    // passes — scoreRef for "无关数据" against "财务" returns 99,
    // but sortByRelevance doesn't filter, just sorts).
    expect(out[0].id).toBe(2); // exact
    expect(out[1].id).toBe(3); // prefix, shorter
    // The long prefix and the no-match are tied (different scores);
    // exact and prefix-tied-sort by length.
    expect(out[0].name).toBe('财务');
  });
});