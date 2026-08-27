/** Admin metrics query hook (批 12).

Thin wrapper around ``useQuery`` for ``GET /admin/metrics``. The
backend route is admin-gated, so a 403 here means the caller is not
an admin — the page component is already wrapped in ``RequireAdmin``
and will bounce non-admins before this fires.

No filter params: the endpoint always returns the full per-DataSource
fleet snapshot. We give the cache a 30 s ``staleTime`` so a tab
returning to the page doesn't trigger an instant refetch; the admin
page also has a manual ``refetch`` button for explicit refresh.
*/

import { useQuery } from '@tanstack/react-query';

import { adminMetricsApi } from '../api';
import { queryKeys } from './keys';

export function useAdminMetrics() {
  return useQuery({
    queryKey: queryKeys.adminMetrics.current(),
    queryFn: () => adminMetricsApi.get(),
    // 30 s — the metrics are point-in-time but a fresh fleet-wide
    // fetch on every navigation is wasteful for an admin dashboard
    // that may sit idle for minutes at a time.
    staleTime: 30_000,
    // We do NOT set ``retry: false`` here — a transient 5xx is
    // worth one retry before showing the error state (mirrors
    // ``useAuditLogs``).
  });
}
