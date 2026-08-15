import { useMutation } from '@tanstack/react-query';

import { explorerApi } from '../api';

/**
 * `useExploreQuery` — a mutation, not a query.
 *
 * `explorerApi.query` returns `{ success, columns, rows, ... }` where
 * `success: false` is a *result*, NOT a thrown error. The endpoint
 * executed the SQL and got back an error message — it didn't fail.
 * If we used `useQuery`, we'd have to either throw inside the queryFn
 * (losing the structured response) or use `select` to normalize.
 *
 * `useMutation` keeps the result in `mutation.data` whether the server
 * said `success: true` or `success: false`; `mutation.isError` fires only
 * on network/HTTP errors. The page reads `data.success` and
 * `data.error` directly.
 */
export function useExploreQuery() {
  return useMutation({
    mutationFn: ({ dataSourceId, sql }: { dataSourceId: number; sql: string }) =>
      explorerApi.query(dataSourceId, sql),
  });
}
