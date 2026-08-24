/**
 * Typed query key factory — single source of truth for cache invalidation.
 *
 * Tuple-typed (not string-typed) so `invalidateQueries({ queryKey: queryKeys.reports.all })`
 * cascades through every nested key (`reports.list`, `reports.detail`, etc.).
 */

export const queryKeys = {
  auth: {
    me: ['auth', 'me'] as const,
  },
  dataSources: {
    all: ['data-sources'] as const,
    lists: () => [...queryKeys.dataSources.all, 'list'] as const,
    list: () => [...queryKeys.dataSources.lists()] as const,
    detail: (id: number) => [...queryKeys.dataSources.all, 'detail', id] as const,
    // Schema-browser key: keyed by (id, schema) so different schema
    // names don't collide in the cache. Schema is the user-supplied
    // override or the data source's default ("public" / "main").
    schema: (id: number, schema: string | undefined) =>
      [...queryKeys.dataSources.all, 'schema', id, schema ?? '__default__'] as const,
    // ACL key (批 9.3) — grants list per data source. Owner-or-admin only.
    acl: (id: number) => [...queryKeys.dataSources.all, 'acl', id] as const,
  },
  users: {
    all: ['users'] as const,
    list: () => [...queryKeys.users.all, 'list'] as const,
  },
  reports: {
    all: ['reports'] as const,
    lists: () => [...queryKeys.reports.all, 'list'] as const,
    // `filters ?? {}` keeps `useReports()` and `useReports({})` on the
    // same cache key.
    list: (filters?: { is_active?: boolean; data_source_id?: number }) =>
      [...queryKeys.reports.lists(), filters ?? {}] as const,
    details: () => [...queryKeys.reports.all, 'detail'] as const,
    detail: (id: number) => [...queryKeys.reports.details(), id] as const,
    preview: (id: number) => [...queryKeys.reports.all, 'preview', id] as const,
    // Shares key (批 9.4) — per-report share list. Owner-or-admin only.
    shares: (id: number) => [...queryKeys.reports.all, 'shares', id] as const,
  },
  scheduler: {
    all: ['scheduler'] as const,
    status: () => [...queryKeys.scheduler.all, 'status'] as const,
    job: (reportId: number) => [...queryKeys.scheduler.all, 'job', reportId] as const,
  },
  explorer: {
    all: ['explorer'] as const,
    // Kept for completeness; the explorer mutation does not currently
    // populate a cache, but a future "last result" cache would key here.
    lastResult: (dataSourceId: number, sqlHash: string) =>
      [...queryKeys.explorer.all, 'lastResult', dataSourceId, sqlHash] as const,
  },
  jobs: {
    all: ['jobs'] as const,
    detail: (jobId: number) => [...queryKeys.jobs.all, 'detail', jobId] as const,
    forReport: (reportId: number) => [...queryKeys.jobs.all, 'report', reportId] as const,
  },
  parameters: {
    all: ['parameters'] as const,
    list: (reportId: number) => [...queryKeys.parameters.all, 'list', reportId] as const,
  },
  // Audit log (批 9.6) — admin-only. Filters go into the key so
  // different filter combos don't share a cache entry.
  auditLog: {
    all: ['audit-log'] as const,
    list: (filters?: {
      actor_user_id?: number;
      action?: string;
      target_type?: string;
      target_id?: number;
      since?: string;
      until?: string;
      limit?: number;
      offset?: number;
    }) => [...queryKeys.auditLog.all, 'list', filters ?? {}] as const,
  },
} as const;
