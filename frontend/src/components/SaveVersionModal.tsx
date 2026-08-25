import { Modal, Input, Form, message } from 'antd';
import { useState } from 'react';
import { useCreateReportVersion } from '../queries/useReportVersions';
import { formatError } from '../utils/error';

interface Props {
  open: boolean;
  reportId: number;
  onClose: () => void;
  onCreated?: () => void;
}

export function SaveVersionModal({ open, reportId, onClose, onCreated }: Props) {
  const [label, setLabel] = useState('');
  const mutation = useCreateReportVersion(reportId);

  const handleOk = async () => {
    try {
      await mutation.mutateAsync({ label: label.trim() || undefined });
      message.success('版本已保存');
      setLabel('');
      onClose();
      onCreated?.();
    } catch (err) {
      message.error(formatError(err, '保存版本失败'));
    }
  };

  return (
    <Modal
      title="保存为版本"
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      confirmLoading={mutation.isPending}
      okText="保存"
      cancelText="取消"
    >
      <Form layout="vertical">
        <Form.Item label="版本标签（可选）" help="例如「Q1 报表」「季度 v1.0」；最多 255 字符">
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={255}
            placeholder="留空将自动以当前时间命名"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
