import { Modal, message } from 'antd';
import { useRestoreReportVersion } from '../../queries/useReportVersions';
import type { ReportVersionSummary } from '../../types';

interface Props {
  open: boolean;
  reportId: number;
  version: ReportVersionSummary | null;
  onClose: () => void;
  onRestored?: () => void;
}

export function RestoreConfirmModal({ open, reportId, version, onClose, onRestored }: Props) {
  const mutation = useRestoreReportVersion(reportId);

  const handleOk = async () => {
    if (!version) return;
    try {
      await mutation.mutateAsync(version.id);
      message.success(`已恢复到 v${version.version_number}`);
      onClose();
      onRestored?.();
    } catch {
      message.error('恢复失败');
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
