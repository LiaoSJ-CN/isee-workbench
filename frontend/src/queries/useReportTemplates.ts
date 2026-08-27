/**
 * 批 13 — Report template marketplace hooks.
 *
 * Three hooks, mirroring the shape of the report CRUD pair in
 * ``useReports.ts``:
 *
 * - ``useReportTemplates(filters)`` — gallery list query. Filter
 *   object is baked into the cache key via ``queryKeys.reportTemplates.list``,
 *   so different filter combos hit different cache entries.
 * - ``useSaveAsTemplate`` — publishes a report into the template
 *   pool. Owner-or-admin only; backend enforces it. Invalidates the
 *   template list (new row appears) and the report list (source row
 *   didn't change but the next render of /reports doesn't need a
 *   separate refetch).
 * - ``useForkReport`` — forks a template into a personal Report.
 *   Read ACL on the template is enough. Invalidates both lists —
 *   the fork is a new private report so ``reports.all`` needs to
 *   refetch, and the template list is unaffected but cheap to
 *   invalidate.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { templatesApi } from '../api';
import type {
  ForkFromTemplateRequest,
  Report,
  ReportTemplatesFilters,
  SaveAsTemplateRequest,
} from '../types';
import { queryKeys } from './keys';

export function useReportTemplates(filters?: ReportTemplatesFilters) {
  return useQuery({
    queryKey: queryKeys.reportTemplates.list(filters),
    queryFn: () => templatesApi.list(filters),
  });
}

/**
 * Save-as-template mutation — returns the new template row so the
 * caller can route to its detail view if desired. In practice the
 * ReportEditor "另存为模板" button just toasts and stays put.
 */
export function useSaveAsTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ reportId, payload }: { reportId: number; payload: SaveAsTemplateRequest }) =>
      templatesApi.saveAsTemplate(reportId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.reportTemplates.all });
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
    },
  });
}

/**
 * Fork-from-template mutation — returns the new private Report so
 * the caller can navigate straight into its detail view (the
 * ReportTemplates "使用此模板" button does exactly this).
 */
export function useForkReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      templateId,
      payload,
    }: {
      templateId: number;
      payload?: ForkFromTemplateRequest;
    }) => templatesApi.fork(templateId, payload ?? {}),
    onSuccess: (fork: Report) => {
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
      // The fork itself isn't a template, so the template list
      // doesn't strictly need invalidation — but the count badge
      // in the menu might be derived from it later. Cheap to
      // invalidate, so do it.
      void qc.invalidateQueries({ queryKey: queryKeys.reportTemplates.all });
      return fork;
    },
  });
}
