import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { schedulerApi } from '../api';
import { queryKeys } from './keys';

/**
 * `useSchedulerStatus` — polls every 5 s while the tab is in the
 * foreground. The sidecar resyncs the scheduler DB every 30 s, so 5 s
 * gives a snappy UI without spamming the backend. `refetchIntervalInBackground`
 * is `false` so hidden tabs don't poll.
 */
export function useSchedulerStatus() {
  return useQuery({
    queryKey: queryKeys.scheduler.status(),
    queryFn: schedulerApi.getStatus,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
  });
}

/**
 * `useSchedulerJob(reportId)` — 404 is a normal "no job" state, not an error.
 * `retry: false` keeps the noise out of devtools.
 */
export function useSchedulerJob(reportId: number | null | undefined) {
  return useQuery({
    queryKey: queryKeys.scheduler.job(reportId ?? -1),
    queryFn: () => schedulerApi.getJob(reportId as number),
    enabled: reportId != null,
    retry: false,
  });
}

interface CreateJobInput {
  reportId: number;
  cronExpression: string;
  scheduleDescription?: string;
  notificationConfig?: Record<string, unknown> | null;
  isActive?: boolean;
}

/**
 * `useCreateSchedulerJob` — creates a job AND (because the backend
 * persists `is_scheduled` / `cron_expression` / `is_active` on the
 * report row) invalidates BOTH `scheduler.all` AND `reports.all`.
 * Without the reports invalidation, the page's reports table would
 * show stale `is_scheduled` / `cron_expression` flags.
 */
export function useCreateSchedulerJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateJobInput) =>
      schedulerApi.createJob(
        input.reportId,
        input.cronExpression,
        input.scheduleDescription,
        input.notificationConfig,
        input.isActive ?? true,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.scheduler.all });
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
    },
  });
}

/**
 * `useDeleteSchedulerJob` — same dual invalidation (the backend clears
 * `is_scheduled` on the report row on delete).
 */
export function useDeleteSchedulerJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reportId: number) => schedulerApi.deleteJob(reportId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.scheduler.all });
      void qc.invalidateQueries({ queryKey: queryKeys.reports.all });
    },
  });
}

export function useSyncScheduler() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => schedulerApi.sync(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.scheduler.all });
    },
  });
}
