import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { dataSourceApi } from '../api';
import type {
  DataSource,
  DataSourceCreate,
  DataSourceGrant,
  DataSourceGrantCreate,
  DashboardRef,
  ReportRef,
} from '../types';
import { queryKeys } from './keys';

/**
 * `useDataSources` — list query shared by DataSourceList, ReportList,
 * ReportEditor, DataExplorer. Cross-page dedup is automatic via the
 * `dataSources.list()` cache key.
 */
export function useDataSources() {
  return useQuery({
    queryKey: queryKeys.dataSources.list(),
    queryFn: dataSourceApi.list,
  });
}

export function useDataSource(id: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.dataSources.detail(id ?? -1),
    queryFn: () => dataSourceApi.get(id as number),
    enabled: id != null,
  });
}

export function useCreateDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: DataSourceCreate) => dataSourceApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.all });
    },
  });
}

/**
 * Optimistic `useUpdateDataSource` — mirrors `useUpdateReport`'s snapshot/
 * rollback pattern. The list and detail caches both get the patch so that
 * any consumer (DataSourceList, ReportEditor, DataExplorer) sees the
 * updated name/is_active instantly.
 */
export function useUpdateDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<DataSourceCreate> }) =>
      dataSourceApi.update(id, payload),
    onMutate: async ({ id, payload }) => {
      await qc.cancelQueries({ queryKey: queryKeys.dataSources.detail(id) });
      const prevDetail = qc.getQueryData<DataSource>(queryKeys.dataSources.detail(id));
      if (prevDetail) {
        qc.setQueryData<DataSource>(queryKeys.dataSources.detail(id), {
          ...prevDetail,
          ...payload,
        });
      }
      const listKey = queryKeys.dataSources.list();
      await qc.cancelQueries({ queryKey: listKey });
      const prevList = qc.getQueryData<DataSource[]>(listKey);
      if (prevList) {
        qc.setQueryData<DataSource[]>(
          listKey,
          prevList.map((ds) => (ds.id === id ? { ...ds, ...payload } : ds)),
        );
      }
      return { prevDetail, prevList, id };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prevDetail && ctx.id != null) {
        qc.setQueryData(queryKeys.dataSources.detail(ctx.id), ctx.prevDetail);
      }
      if (ctx?.prevList) {
        qc.setQueryData(queryKeys.dataSources.list(), ctx.prevList);
      }
    },
    onSettled: (_data, _err, { id }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.detail(id) });
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.all });
    },
  });
}

/**
 * Optimistic `useDeleteDataSource` — the row disappears from the list
 * immediately. If the DELETE fails, the row is restored from the snapshot.
 */
export function useDeleteDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => dataSourceApi.delete(id),
    onMutate: async (id) => {
      const listKey = queryKeys.dataSources.list();
      await qc.cancelQueries({ queryKey: listKey });
      const prevList = qc.getQueryData<DataSource[]>(listKey);
      if (prevList) {
        qc.setQueryData<DataSource[]>(
          listKey,
          prevList.filter((ds) => ds.id !== id),
        );
      }
      return { prevList };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prevList) {
        qc.setQueryData(queryKeys.dataSources.list(), ctx.prevList);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.all });
    },
  });
}

/** Connection-test — no cache to invalidate; the result is surfaced via message. */
export function useTestDataSource() {
  return useMutation({
    mutationFn: (id: number) => dataSourceApi.test(id),
  });
}

/** Clone (batch 10.3) — returns the new DataSource so it can be
 *  navigated into. Invalidates the list query so the new row shows
 *  up immediately. */
export function useCloneDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name?: string }) => dataSourceApi.clone(id, name),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.all });
    },
  });
}

/** Convenience: returns the data-source list synchronously if already cached. */
export function useCachedDataSources(): DataSource[] | undefined {
  const qc = useQueryClient();
  return qc.getQueryData<DataSource[]>(queryKeys.dataSources.list());
}

// ---- ACL (批 9.3) ----

/** List grants on a data source. Owner-or-admin only — backend
 *  returns 404 for non-owners/non-admins. Disabled when ``dsId`` is
 *  null so the modal doesn't fire the request before the row is picked. */
export function useDataSourceAcl(dsId: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.dataSources.acl(dsId ?? -1),
    queryFn: () => dataSourceApi.listAcl(dsId as number),
    enabled: dsId != null,
    retry: false, // 404 means "no access" — don't retry-loop.
  });
}

/** Upsert a grant (POST). Invalidates the ACL cache so the list
 *  refreshes after creation/update. */
export function useUpsertDataSourceAcl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ dsId, payload }: { dsId: number; payload: DataSourceGrantCreate }) =>
      dataSourceApi.createAcl(dsId, payload),
    onSuccess: (_grant, { dsId }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.acl(dsId) });
    },
  });
}

/** Revoke a grant (DELETE). Invalidates the ACL cache so the row
 *  disappears from the table immediately. */
export function useDeleteDataSourceAcl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ grantId }: { dsId: number; grantId: number }) =>
      dataSourceApi.revokeAcl(grantId),
    onSuccess: (_data, { dsId }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.acl(dsId) });
    },
  });
}

// ``useUsers`` lives in its own file (A3, post-批-report-versioning) —
// re-exported here for backward compatibility with the share-modal
// pages that already import it from ``./useDataSources``. New consumers
// should import from ``./useUsers`` directly.
export { useUsers } from './useUsers';

// Re-export so callers can grab the grant row type alongside the hooks
// without importing from two places.
export type { DataSourceGrant };

// ---- Reverse-link queries (D 双向 link) ----
// Two listings keyed by data-source id. ``enabled: dsId != null`` keeps
// the request cold until the parent row is known; ``retry: false`` so
// an ACL 404 doesn't trigger react-query's retry loop.

/** Reports whose ``data_source_id`` is this DS. */
export function useReferencingReports(
  dsId: number | null | undefined,
): ReturnType<typeof useQuery<ReportRef[]>> {
  return useQuery<ReportRef[]>({
    queryKey: queryKeys.dataSources.referencingReports(dsId ?? -1),
    queryFn: () => dataSourceApi.listReferencingReports(dsId as number),
    enabled: dsId != null,
    retry: false,
  });
}

/** Dashboards that touch this DS — directly via chart items or
 *  transitively via report items. Deduped by ``Dashboard.id``. */
export function useReferencingDashboards(
  dsId: number | null | undefined,
): ReturnType<typeof useQuery<DashboardRef[]>> {
  return useQuery<DashboardRef[]>({
    queryKey: queryKeys.dataSources.referencingDashboards(dsId ?? -1),
    queryFn: () => dataSourceApi.listReferencingDashboards(dsId as number),
    enabled: dsId != null,
    retry: false,
  });
}
