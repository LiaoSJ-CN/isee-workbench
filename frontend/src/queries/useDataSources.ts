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

export function useUpdateDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<DataSourceCreate> }) =>
      dataSourceApi.update(id, payload),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.all });
      void qc.invalidateQueries({ queryKey: queryKeys.dataSources.detail(id) });
    },
  });
}

export function useDeleteDataSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => dataSourceApi.delete(id),
    onSuccess: () => {
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
