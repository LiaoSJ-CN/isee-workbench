import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { parametersApi } from '../api';
import type {
  ReportParameter,
  ReportParameterCreate,
  ReportParameterUpdate,
} from '../types';
import { queryKeys } from './keys';

/**
 * `useReportParameters(reportId)` — list query, ordered by `order_index`
 * server-side (matches the order they appear in the form). Disabled
 * when `reportId` is null (route not yet resolved).
 */
export function useReportParameters(reportId: number | null | undefined) {
  return useQuery({
    queryKey: reportId != null
      ? queryKeys.parameters.list(reportId)
      : queryKeys.parameters.list(-1),
    queryFn: () => parametersApi.list(reportId as number),
    enabled: reportId != null,
  });
}

/**
 * `useCreateReportParameter(reportId)` — POST then invalidate the
 * parameter list so the form re-renders with the new field. No
 * optimistic update — the list view is editor-only and brief latency
 * keeps the implementation simple.
 */
export function useCreateReportParameter(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ReportParameterCreate) =>
      parametersApi.create(reportId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.parameters.list(reportId) });
    },
  });
}

/**
 * `useUpdateReportParameter(reportId)` — PUT (full replacement
 * semantics; missing fields are dropped server-side via
 * `exclude_unset=True`).
 */
export function useUpdateReportParameter(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      paramId,
      payload,
    }: {
      paramId: number;
      payload: ReportParameterUpdate;
    }) => parametersApi.update(reportId, paramId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.parameters.list(reportId) });
    },
  });
}

/**
 * `useDeleteReportParameter(reportId)` — DELETE 204 then invalidate
 * the parameter list.
 */
export function useDeleteReportParameter(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (paramId: number) => parametersApi.delete(reportId, paramId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.parameters.list(reportId) });
    },
  });
}

// Re-export the type so consumers can `import { ReportParameter } from '../queries/useParameters'`
// without reaching into `../types` (purely ergonomic — keeps the import
// list shorter in callers that already pull in other hooks).
export type { ReportParameter };