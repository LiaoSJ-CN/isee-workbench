import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { reportApi } from '../api';
import type {
  Report,
  ReportCreate,
  ReportItem,
  ReportItemCreate,
  ReportItemUpdate,
  ReportShare,
  ReportShareCreate,
  ReportUpdate,
} from '../types';
import { queryKeys } from './keys';

interface ReportListFilters {
  is_active?: boolean;
  data_source_id?: number;
}

/**
 * `useReports` — list query shared by ReportList, Scheduler (with
 * `is_active: true`), and any future listing surface. The optional
 * `filters` are baked into the cache key, so different filter combos
 * hit different cache entries.
 */
export function useReports(filters?: ReportListFilters) {
  return useQuery({
    queryKey: queryKeys.reports.list(filters),
    queryFn: () => reportApi.list(filters),
  });
}

/**
 * `useReport(id)` — detail query shared by ReportEditor + ReportPreview.
 *
 * `refetchOnWindowFocus: false` so refocusing the tab doesn't clobber
 * an in-progress edit. The user must hit Save explicitly.
 */
export function useReport(id: number | null | undefined) {
  return useQuery({
    queryKey: id != null ? queryKeys.reports.detail(id) : queryKeys.reports.detail(-1),
    queryFn: () => reportApi.get(id as number),
    enabled: id != null,
    refetchOnWindowFocus: false,
  });
}

/**
 * `useReportPreviewHtml` — lazy query for the HTML preview.
 *
 * `staleTime: Infinity` because previews are point-in-time snapshots;
 * `gcTime: 30_000` so the (potentially large) HTML string releases quickly.
 */
export function useReportPreviewHtml(
  id: number | null | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: id != null ? queryKeys.reports.preview(id) : queryKeys.reports.preview(-1),
    queryFn: () => reportApi.previewHtml(id as number),
    enabled: enabled && id != null,
    retry: false,
    staleTime: Infinity,
    gcTime: 30_000,
  });
}

export function useCreateReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ReportCreate) => reportApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
    },
  });
}

/**
 * Optimistic `useDeleteReport` — row vanishes from every list cache
 * (including filter-specific ones) immediately. Restored from snapshot
 * on error.
 */
export function useDeleteReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => reportApi.delete(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: queryKeys.reports.all });
      const snapshots: { key: readonly unknown[]; data: unknown }[] = [];
      // Capture and patch every cached list (covers filter variants).
      qc.getQueryCache().findAll({ queryKey: queryKeys.reports.lists() }).forEach((q) => {
        const prev = q.state.data as Report[] | undefined;
        if (prev) {
          snapshots.push({ key: q.queryKey, data: prev });
          qc.setQueryData<Report[]>(
            q.queryKey,
            prev.filter((r) => r.id !== id),
          );
        }
      });
      return { snapshots };
    },
    onError: (_err, _vars, ctx) => {
      ctx?.snapshots.forEach(({ key, data }) => {
        qc.setQueryData(key, data);
      });
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
    },
  });
}

/** Duplicate (batch 10.3) — returns the new Report so the caller can
 *  navigate straight into the editor. Invalidates the list cache so
 *  the new row appears on the next render. */
export function useDuplicateReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name?: string }) =>
      reportApi.duplicate(id, name),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
    },
  });
}

/**
 * Optimistic `useUpdateReport` — replaces the hand-rolled
 * snapshot/rollback in `ReportEditor.tsx:485-505`.
 *
 * Flow:
 *  - `onMutate`: cancel in-flight detail refetch, snapshot cache, write
 *    the optimistic merge into the cache, return the snapshot for rollback.
 *  - `onError`: restore the snapshot.
 *  - `onSettled`: invalidate the detail (re-fetch truth) and the list
 *    (so ReportList picks up the new name/description/is_active).
 */
export function useUpdateReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: ReportUpdate }) =>
      reportApi.update(id, payload),
    onMutate: async ({ id, payload }) => {
      await qc.cancelQueries({ queryKey: queryKeys.reports.detail(id) });
      const prev = qc.getQueryData<Report>(queryKeys.reports.detail(id));
      if (prev) {
        qc.setQueryData<Report>(queryKeys.reports.detail(id), { ...prev, ...payload });
      }
      return { prev, id };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev && ctx.id != null) {
        qc.setQueryData(queryKeys.reports.detail(ctx.id), ctx.prev);
      }
    },
    onSettled: (_data, _err, { id }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.detail(id) });
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
    },
  });
}

// ----- Report items -----

/**
 * Optimistic `useCreateReportItem` — new item appended to the detail's
 * `items` array with a temporary negative id so the user sees it instantly.
 * The temp id is replaced when the server response lands and the cache
 * is invalidated by `onSettled`.
 */
export function useCreateReportItem(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ReportItemCreate) => reportApi.createItem(reportId, payload),
    onMutate: async (payload) => {
      const key = queryKeys.reports.detail(reportId);
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Report>(key);
      if (prev) {
        const tempItem = {
          ...payload,
          id: -Date.now(),
          order_index: (prev.items?.length ?? 0) + 1,
        } as unknown as ReportItem;
        qc.setQueryData<Report>(key, {
          ...prev,
          items: [...(prev.items ?? []), tempItem],
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(queryKeys.reports.detail(reportId), ctx.prev);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.detail(reportId) });
    },
  });
}

export function useUpdateReportItem(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, payload }: { itemId: number; payload: ReportItemUpdate }) =>
      reportApi.updateItem(reportId, itemId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.detail(reportId) });
    },
  });
}

/**
 * Optimistic `useDeleteReportItem` — the item vanishes from the detail's
 * `items` array immediately. Restored from snapshot on error.
 */
export function useDeleteReportItem(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) => reportApi.deleteItem(reportId, itemId),
    onMutate: async (itemId) => {
      const key = queryKeys.reports.detail(reportId);
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Report>(key);
      if (prev) {
        qc.setQueryData<Report>(key, {
          ...prev,
          items: (prev.items ?? []).filter((it) => it.id !== itemId),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(queryKeys.reports.detail(reportId), ctx.prev);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.detail(reportId) });
    },
  });
}

/**
 * Optimistic `useReorderReportItems` — reorders items in the detail cache
 * immediately using the caller-supplied payload. Note: `ReportEditor` keeps
 * its own edit buffer in sync separately via `setBuffer`, but any other
 * consumer of `reports.detail(reportId)` (currently none) would see the
 * new order instantly.
 */
export function useReorderReportItems(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: { item_id: number; order_index: number }[]) =>
      reportApi.reorderItems(reportId, items),
    onMutate: async (items) => {
      const key = queryKeys.reports.detail(reportId);
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Report>(key);
      if (prev) {
        const orderMap = new Map(items.map((i) => [i.item_id, i.order_index]));
        qc.setQueryData<Report>(key, {
          ...prev,
          items: (prev.items ?? [])
            .map((it) =>
              orderMap.has(it.id) ? { ...it, order_index: orderMap.get(it.id) as number } : it,
            )
            .sort((a, b) => a.order_index - b.order_index),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(queryKeys.reports.detail(reportId), ctx.prev);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.detail(reportId) });
    },
  });
}

// ----- Generation / download -----

export function useDownloadReport() {
  return useMutation({
    mutationFn: ({
      reportId,
      format,
      filename,
    }: {
      reportId: number;
      format: 'excel' | 'html';
      filename: string;
    }) => reportApi.download(reportId, format, filename),
  });
}

// ---- Shares (批 9.4) ----

/** List shares on a report. Owner-or-admin only — backend returns 404
 *  for write-grantees. Disabled when ``reportId`` is null so the
 *  modal doesn't fire the request before the row is picked. */
export function useReportShares(reportId: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.reports.shares(reportId ?? -1),
    queryFn: () => reportApi.listShares(reportId as number),
    enabled: reportId != null,
    retry: false,
  });
}

/** Upsert a share (POST). Invalidates the per-report shares cache so
 *  the table refreshes after creation/update. */
export function useUpsertReportShare() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      reportId,
      payload,
    }: {
      reportId: number;
      payload: ReportShareCreate;
    }) => reportApi.createShare(reportId, payload),
    onSuccess: (_share, { reportId }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.shares(reportId) });
    },
  });
}

/** Revoke a share (DELETE). Invalidates the per-report shares cache
 *  so the row disappears from the table immediately. */
export function useDeleteReportShare() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ shareId }: { reportId: number; shareId: number }) =>
      reportApi.revokeShare(shareId),
    onSuccess: (_data, { reportId }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.shares(reportId) });
    },
  });
}

// Re-export so callers can grab the share row type alongside the
// hooks without importing from two places.
export type { ReportShare };
