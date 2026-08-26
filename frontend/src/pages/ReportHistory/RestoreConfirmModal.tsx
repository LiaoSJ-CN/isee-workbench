import { Modal, message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { useRestoreReportVersion } from '../../queries/useReportVersions';
import type { ReportVersionSummary } from '../../types';

interface Props {
  open: boolean;
  reportId: number;
  version: ReportVersionSummary | null;
  /**
   * ``Report.updated_at`` captured when the history page loaded.
   * A5: sent as ``expected_updated_at`` on restore so the server
   * can 409 if the live Report has been touched in the interim.
   * ``null``/omitted → server skips the check (backward compat).
   */
  currentUpdatedAt?: string | null;
  onClose: () => void;
  onRestored?: () => void;
}

export function RestoreConfirmModal({
  open,
  reportId,
  version,
  currentUpdatedAt,
  onClose,
  onRestored,
}: Props) {
  const mutation = useRestoreReportVersion(reportId);
  const qc = useQueryClient();

  const handleOk = async () => {
    if (!version) return;
    try {
      await mutation.mutateAsync({
        versionId: version.id,
        expectedUpdatedAt: currentUpdatedAt ?? null,
      });
      message.success(`已恢复到 v${version.version_number}`);
      onClose();
      onRestored?.();
    } catch (err: unknown) {
      // A5: surface the server's stale-state message instead of a
      // generic error, and invalidate the queries so the next click
      // picks up the fresh updated_at.
      const axiosLike = err as { response?: { status?: number; data?: { detail?: { message?: string } } } };
      if (axiosLike?.response?.status === 409) {
        const msg =
          axiosLike.response.data?.detail?.message ??
          '报表已被修改，请刷新后重试';
        message.warning(msg);
        qc.invalidateQueries({ queryKey: ['reports', reportId] });
        qc.invalidateQueries({ queryKey: ['report-versions', reportId] });
      } else {
        message.error('恢复失败');
      }
    }
  };

  return (
    <Modal
      title={`恢复到 v${version?.version_number}？`}
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      confirmLoading={mutation.isPending}
      okText="确认恢复"
      cancelText="取消"
      okButtonProps={{ danger: true }}
    >
      <p>
        当前报表将被 <strong>v{version?.version_number}</strong> 的快照覆盖。
      </p>
      <p>操作不可撤销——但恢复前的状态会保留在历史记录中（如果你之前手动保存过版本）。</p>
    </Modal>
  );
}