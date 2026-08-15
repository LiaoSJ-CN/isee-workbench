import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { reportApi } from '../api';
import type {
  Report,
  ReportCreate,
  ReportItemCreate,
  ReportItemUpdate,
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

export function useDeleteReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => reportApi.delete(id),
    onSuccess: () => {
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

export function useCreateReportItem(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ReportItemCreate) => reportApi.createItem(reportId, payload),
    onSuccess: () => {
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

export function useDeleteReportItem(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) => reportApi.deleteItem(reportId, itemId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.detail(reportId) });
    },
  });
}

export function useReorderReportItems(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: { item_id: number; order_index: number }[]) =>
      reportApi.reorderItems(reportId, items),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.detail(reportId) });
    },
  });
}

// ----- Generation / download -----

export function useGenerateReport() {
  return useMutation({
    mutationFn: ({
      reportId,
      outputFormat,
      parameters,
    }: {
      reportId: number;
      outputFormat: 'excel' | 'html';
      parameters?: Record<string, unknown>;
    }) => reportApi.generate(reportId, outputFormat, parameters),
  });
}

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
