import { useQuery } from '@tanstack/react-query';

import { dataSourceApi } from '../api';
import type { DataSourceSchema } from '../types';
import { queryKeys } from './keys';

/**
 * Schema-browser query — fetches the list of user tables + columns for
 * a data source. Backed by ``GET /data-sources/{id}/schema``.
 *
 * The query is disabled until ``dataSourceId`` is known — DataExplorer
 * may render before the user picks a source.
 *
 * Schema name resolution is server-side: pass ``schema`` to override,
 * otherwise the server falls back to the data source's ``schema_name``
 * (or ``public`` / ``main`` for the dialect default).
 */
export function useDataSourceSchema(
  dataSourceId: number | null | undefined,
  schema?: string,
) {
  return useQuery<DataSourceSchema>({
    queryKey: queryKeys.dataSources.schema(dataSourceId ?? -1, schema),
    queryFn: () => dataSourceApi.schema(dataSourceId as number, schema),
    enabled: dataSourceId != null,
    // Schema only changes when the source or the schema name changes,
    // so 5 minutes is plenty — the user can manually refresh too.
    staleTime: 5 * 60 * 1000,
  });
}