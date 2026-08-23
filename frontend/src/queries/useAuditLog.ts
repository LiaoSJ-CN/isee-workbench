import { useQuery } from '@tanstack/react-query';

import { auditLogApi } from '../api';
import type { AuditLogFilters } from '../types';
import { queryKeys } from './keys';

/**
 * `useAuditLogs(filters)` — admin-only audit log reader (批 9.6).
 *
 * The backend route is gated by ``admin_required`` so a 403 here
 * means the caller is not an admin. We do NOT set ``retry: false``
 * because a transient 5xx / 502 is worth one retry before the UI
 * surfaces an error; a 403 will land on the page as a toast instead
 * and is not retried per the global default (``retry: 1``).
 *
 * ``filters`` is the source of truth for both the cache key and the
 * HTTP query. The page component owns the filter form state and
 * invalidates / re-fetches by passing a fresh filters object.
 */
export function useAuditLogs(filters?: AuditLogFilters) {
  return useQuery({
    queryKey: queryKeys.auditLog.list(filters),
    queryFn: () => auditLogApi.list(filters),
  });
}
