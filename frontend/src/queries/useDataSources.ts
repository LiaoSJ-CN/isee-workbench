import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { dataSourceApi } from '../api';
import type { DataSource, DataSourceCreate } from '../types';
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
        qc.setQueryData<DataSource>(queryKeys.dataSources.detail(id), { ...prevDetail, ...payload });
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

/** Convenience: returns the data-source list synchronously if already cached. */
export function useCachedDataSources(): DataSource[] | undefined {
  const qc = useQueryClient();
  return qc.getQueryData<DataSource[]>(queryKeys.dataSources.list());
}
