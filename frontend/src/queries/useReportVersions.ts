import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { reportVersionsApi, type ReportVersionCreate } from '../api';
import type { ReportVersionDiff, ReportVersionResponse, ReportVersionSummary } from '../types';

const KEYS = {
  list: (reportId: number) => ['report-versions', reportId] as const,
  detail: (reportId: number, versionId: number) =>
    ['report-versions', reportId, versionId] as const,
  diff: (reportId: number, versionId: number, against: number | 'current') =>
    ['report-versions', reportId, versionId, 'diff', against] as const,
};

export function useReportVersions(reportId: number | null) {
  return useQuery<ReportVersionSummary[]>({
    queryKey: KEYS.list(reportId ?? 0),
    queryFn: () => reportVersionsApi.list(reportId!),
    enabled: reportId !== null,
  });
}

export function useReportVersion(reportId: number | null, versionId: number | null) {
  return useQuery<ReportVersionResponse>({
    queryKey: KEYS.detail(reportId ?? 0, versionId ?? 0),
    queryFn: () => reportVersionsApi.get(reportId!, versionId!),
    enabled: reportId !== null && versionId !== null,
  });
}

export function useReportVersionDiff(
  reportId: number | null,
  versionId: number | null,
  against: number | 'current' = 'current',
) {
  return useQuery<ReportVersionDiff>({
    queryKey: KEYS.diff(reportId ?? 0, versionId ?? 0, against),
    queryFn: () => reportVersionsApi.diff(reportId!, versionId!, against),
    enabled: reportId !== null && versionId !== null,
  });
}

export function useCreateReportVersion(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ReportVersionCreate) => reportVersionsApi.create(reportId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.list(reportId) }),
  });
}

export function useRestoreReportVersion(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      versionId,
      expectedUpdatedAt,
    }: {
      versionId: number;
      expectedUpdatedAt?: string | null;
    }) =>
      reportVersionsApi.restore(reportId, versionId, { expectedUpdatedAt }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEYS.list(reportId) });
      qc.invalidateQueries({ queryKey: ['reports', reportId] });
    },
  });
}

export function useDeleteReportVersion(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (versionId: number) => reportVersionsApi.delete(reportId, versionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.list(reportId) }),
  });
}

export function usePinReportVersion(reportId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ versionId, pinned }: { versionId: number; pinned: boolean }) =>
      reportVersionsApi.pin(reportId, versionId, pinned),
    onSuccess: () => {
      // Invalidate both list and any open diff views so the new
      // ``is_pinned`` value propagates everywhere.
      qc.invalidateQueries({ queryKey: KEYS.list(reportId) });
      qc.invalidateQueries({ queryKey: ['report-versions', reportId] });
    },
  });
}
