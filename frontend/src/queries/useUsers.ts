/**
 * ``useUsers`` — fetch the lightweight ``id / username / role`` projection
 * for every user in the metadata database (A3, post-批-report-versioning).
 *
 * Used by:
 *
 *   - The share modals (data source + report): only fetch when the modal
 *     actually opens (``enabled: isOpen``); see ``ReportShareModal`` /
 *     ``DataSourceShareModal`` for the lookup.
 *   - ``AuditLogPage`` (批 9.6): eager fetch to display actor usernames
 *     alongside each audit row.
 *   - ``VersionTable`` (A3, post-批-report-versioning): eager fetch to
 *     resolve ``ReportVersionSummary.created_by`` (raw id) into a
 *     human-readable ``username`` instead of showing ``"5"``.
 *
 * The hook is intentionally lazy-by-default — passing no options means
 * the query is disabled — so a host page that imports it speculatively
 * (e.g. always-on AppShell sidebar) does not pay for the round-trip
 * until a real consumer asks for it. Pages that need the data eagerly
 * (AuditLog, VersionTable) pass ``{ enabled: true }`` explicitly.
 */

import { useQuery } from '@tanstack/react-query';

import { usersApi } from '../api';
import { queryKeys } from './keys';

export function useUsers(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.users.list(),
    queryFn: usersApi.list,
    enabled: options?.enabled ?? false,
    retry: false,
    staleTime: 60_000,
  });
}