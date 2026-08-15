import { useMutation, useQuery } from '@tanstack/react-query';

import { jobsApi } from '../api';
import type { ReportJobCreate } from '../types';
import { queryKeys } from './keys';

/**
 * `useJobStatus(jobId)` — polls every 2 s while the job is still in
 * flight (pending / running). The polling interval drops to `false`
 * the moment the backend reports `done` or `failed` so the UI stops
 * hitting the server once the result is known.
 *
 * `refetchIntervalInBackground: false` matches `useSchedulerStatus` —
 * hidden tabs don't burn requests on a status check that the user
 * can't see anyway.
 *
 * Sentinel `jobId: -1` keeps the cache key stable when the caller has
 * not yet enqueued (i.e. the query is disabled).
 */
export function useJobStatus(jobId: number | null | undefined) {
  return useQuery({
    queryKey: jobId != null ? queryKeys.jobs.detail(jobId) : queryKeys.jobs.detail(-1),
    queryFn: () => jobsApi.get(jobId as number),
    enabled: jobId != null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'done' || status === 'failed' ? false : 2_000;
    },
    refetchIntervalInBackground: false,
  });
}

/**
 * `useEnqueueReportJob(reportId)` — POSTs to `/reports/{id}/jobs` and
 * returns the fresh `ReportJob` row so the caller can immediately
 * start polling. No cache invalidation here: the returned job is the
 * first poll's answer, and the list endpoint (`/reports/{id}/jobs`)
 * is not currently consumed in the UI — add `qc.invalidateQueries`
 * when that surface lands.
 */
export function useEnqueueReportJob(reportId: number | null | undefined) {
  return useMutation({
    mutationFn: (payload: ReportJobCreate = {}) => jobsApi.enqueue(reportId as number, payload),
  });
}