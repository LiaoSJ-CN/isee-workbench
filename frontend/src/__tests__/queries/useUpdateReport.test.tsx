/** Tests for batch 3 — useUpdateReport If-Match / 412 handling.
 *
 * Coverage matrix (5 cases):
 *
 *  1 — Auto-attaches If-Match from cached Report.version.
 *  2 — Caller-supplied ifMatch overrides the cached version.
 *  3 — Skips If-Match when the cache is cold (first save before GET).
 *  4 — 412 → typed ``VersionConflictError`` with ``current`` body.
 *  5 — Non-412 errors propagate untouched and the snapshot rolls back.
 */

import { describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { useUpdateReport } from '../../queries/useReports';
import { queryKeys } from '../../queries/keys';
import * as apiModule from '../../api';
import { isVersionConflict, VersionConflictError, type Report } from '../../types';
import type { AxiosError } from 'axios';

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

function seedCache(qc: QueryClient, report: Report): void {
  qc.setQueryData(queryKeys.reports.detail(report.id), report);
}

const baseReport: Report = {
  id: 42,
  name: 'r',
  description: '',
  data_source_id: 1,
  layout_config: {},
  output_formats: ['excel', 'html'],
  is_active: true,
  is_scheduled: false,
  visibility: 'private',
  owner_user_id: 1,
  is_demo: false,
  is_template: false,
  version: 3,
  items: [],
};

describe('useUpdateReport — If-Match', () => {
  it('attaches If-Match: W/"v<n>" derived from cached Report.version', async () => {
    const updateSpy = vi.fn().mockResolvedValue({ ...baseReport, version: 4 });
    const spy = vi.spyOn(apiModule, 'reportApi', 'get').mockReturnValue({
      ...Object.assign({}, apiModule.reportApi),
      update: updateSpy,
    } as never);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCache(qc, baseReport);
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useUpdateReport(), { wrapper });
    result.current.mutate({ id: 42, payload: { description: 'updated' } });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(updateSpy).toHaveBeenCalledWith(
      42,
      { description: 'updated' },
      { ifMatch: 'W/"v3"' },
    );
    spy.mockRestore();
  });

  it('caller-supplied ifMatch overrides the cached version', async () => {
    const updateSpy = vi.fn().mockResolvedValue({ ...baseReport, version: 99 });
    const spy = vi.spyOn(apiModule, 'reportApi', 'get').mockReturnValue({
      ...Object.assign({}, apiModule.reportApi),
      update: updateSpy,
    } as never);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCache(qc, baseReport);
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useUpdateReport(), { wrapper });
    result.current.mutate({
      id: 42,
      payload: { description: 'force' },
      ifMatch: 'W/"v99"',
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(updateSpy).toHaveBeenCalledWith(
      42,
      { description: 'force' },
      { ifMatch: 'W/"v99"' },
    );
    spy.mockRestore();
  });

  it('omits If-Match when the cache is cold (first save before any GET)', async () => {
    const updateSpy = vi.fn().mockResolvedValue({ ...baseReport, version: 2 });
    const spy = vi.spyOn(apiModule, 'reportApi', 'get').mockReturnValue({
      ...Object.assign({}, apiModule.reportApi),
      update: updateSpy,
    } as never);

    const { result } = renderHook(() => useUpdateReport(), {
      wrapper: makeWrapper(),
    });
    result.current.mutate({ id: 42, payload: { description: 'cold cache' } });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(updateSpy).toHaveBeenCalledWith(
      42,
      { description: 'cold cache' },
      { ifMatch: undefined },
    );
    spy.mockRestore();
  });
});

describe('useUpdateReport — 412 conflict', () => {
  it('translates 412 into VersionConflictError carrying the current state', async () => {
    const current = { ...baseReport, version: 5, description: 'B got here first' };
    const conflictBody = { message: 'Report was modified…', current };
    const axiosErr = {
      isAxiosError: true,
      response: { status: 412, data: { detail: conflictBody } },
    } as AxiosError;
    const updateSpy = vi.fn().mockRejectedValue(axiosErr);
    const spy = vi.spyOn(apiModule, 'reportApi', 'get').mockReturnValue({
      ...Object.assign({}, apiModule.reportApi),
      update: updateSpy,
    } as never);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCache(qc, baseReport);
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useUpdateReport(), { wrapper });
    result.current.mutate({ id: 42, payload: { description: 'A overwrite attempt' } });

    await waitFor(() => expect(result.current.isError).toBe(true));
    const err = result.current.error;
    expect(isVersionConflict(err)).toBe(true);
    if (isVersionConflict(err)) {
      expect(err).toBeInstanceOf(VersionConflictError);
      expect(err.current.version).toBe(5);
      expect(err.current.description).toBe('B got here first');
      expect(err.message).toContain('modified');
    }
    spy.mockRestore();
  });
});

describe('useUpdateReport — non-412 errors', () => {
  it('rolls back the optimistic snapshot on a generic failure', async () => {
    const updateSpy = vi.fn().mockRejectedValue(new Error('network down'));
    const spy = vi.spyOn(apiModule, 'reportApi', 'get').mockReturnValue({
      ...Object.assign({}, apiModule.reportApi),
      update: updateSpy,
    } as never);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedCache(qc, baseReport);
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useUpdateReport(), { wrapper });
    result.current.mutate({ id: 42, payload: { description: 'try once' } });

    await waitFor(() => expect(result.current.isError).toBe(true));
    // Snapshot rolled back — the cached description matches the
    // pre-mutation state.
    const cached = qc.getQueryData<Report>(queryKeys.reports.detail(42));
    expect(cached?.description).toBe(baseReport.description);
    spy.mockRestore();
  });
});