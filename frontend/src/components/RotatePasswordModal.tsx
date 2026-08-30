/** Admin-only "rotate password" modal for DataSource (批 E).
 *
 * Two modes the admin picks between at submit time:
 *
 * - **admin_supplied**: admin types a new plaintext; we POST it and
 *   never echo it back.
 * - **server_generated**: admin confirms; server returns a fresh
 *   24-char random password ONCE. We display it in a copy-friendly
 *   block with a yellow "save it now" alert — closing the modal loses
 *   it forever (the server only stores Fernet ciphertext).
 *
 * The modal owns its open/close lifecycle; the parent just passes
 * ``dataSource`` (null = closed) and ``onClose``. ``pending`` and
 * ``error`` are surfaced from the parent's mutation hook so we don't
 * duplicate React-Query state here.
 */

import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Radio,
  Space,
  Typography,
  message,
} from 'antd';
import { CopyOutlined, KeyOutlined } from '@ant-design/icons';


import { formatError } from '../utils/error';
import type { DataSource, RotatePasswordResponse } from '../types';

const { Text, Paragraph } = Typography;

interface RotatePasswordModalProps {
  open: boolean;
  dataSource: DataSource | null;
  pending: boolean;
  onSubmit: (
    body: { new_password?: string },
  ) => Promise<RotatePasswordResponse>;
  onClose: () => void;
}

type Mode = 'admin_supplied' | 'server_generated';

export function RotatePasswordModal({
  open,
  dataSource,
  pending,
  onSubmit,
  onClose,
}: RotatePasswordModalProps) {
  const [mode, setMode] = useState<Mode>('admin_supplied');
  const [form] = Form.useForm<{ new_password: string }>();
  const [acknowledged, setAcknowledged] = useState(false);
  const [generated, setGenerated] = useState<{
    rotation_method: string;
    generated_password: string;
    rotated_at: string;
  } | null>(null);

  // Reset transient state whenever the modal re-opens (or the
  // target DataSource changes). Without this, switching between
  // rows in the table would leak the previous rotation's plaintext.
  useEffect(() => {
    if (open) {
      setMode('admin_supplied');
      setAcknowledged(false);
      setGenerated(null);
      form.resetFields();
    }
  }, [open, dataSource?.id, form]);

  if (!dataSource) return null;

  const handleSubmit = async () => {
    let plaintext: string | undefined;
    if (mode === 'admin_supplied') {
      try {
        const values = await form.validateFields();
        plaintext = values.new_password;
      } catch {
        // Form validation already surfaces an inline error; just stop.
        return;
      }
    } else {
      // server_generated — require explicit acknowledgement that the
      // plaintext will be shown once and then lost.
      if (!acknowledged) {
        message.warning('请先勾选确认再生成新密码');
        return;
      }
    }

    try {
      const result = await onSubmit(
        plaintext ? { new_password: plaintext } : {},
      );
      if (result.rotation_method === 'server_generated' && result.generated_password) {
        setGenerated({
          rotation_method: result.rotation_method,
          generated_password: result.generated_password,
          rotated_at: result.rotated_at,
        });
        // Stay open so the admin can copy the password. The Modal
        // footer switches to a single "关闭" button at this point
        // (see render).
      } else {
        // admin_supplied — success toast + close.
        message.success(`「${dataSource.name}」密码已更新`);
        onClose();
      }
    } catch (err) {
      // The mutation hook's onError is responsible for surfacing the
      // message; we still log here so the modal layer isn't silent
      // if the parent forgot to wire one up.
      message.error(formatError(err, '密码轮换失败'));
    }
  };

  // After server-generated success, the modal shows the plaintext in
  // a copy block + a yellow "save it now" alert. The footer changes
  // to a single close button so the admin can't accidentally
  // re-submit and generate a new one.
  const renderAfterSuccess = generated && (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert
        type="warning"
        showIcon
        message="请立即复制并妥善保存新密码"
        description={
          <>
            关闭此弹窗后将<strong>无法再次查看</strong>该明文密码 — 服务端只保存 Fernet
            加密的密文。请将新密码同步给运维 / 数据库所有者，并通过「测试」按钮验证连接。
          </>
        }
      />
      <div>
        <Text type="secondary">新密码：</Text>
        <Paragraph
          copyable={{
            icon: [<CopyOutlined key="copy" />, <KeyOutlined key="copied" />],
            text: generated.generated_password,
            tooltips: ['复制', '已复制'],
          }}
          style={{
            marginTop: 4,
            padding: 12,
            background: '#f5f5f5',
            borderRadius: 4,
            fontFamily: 'monospace',
            fontSize: 16,
            letterSpacing: 1,
            wordBreak: 'break-all',
          }}
        >
          <Text code style={{ fontSize: 16 }}>
            {generated.generated_password}
          </Text>
        </Paragraph>
      </div>
      <Text type="secondary" style={{ fontSize: 12 }}>
        轮换时间：{new Date(generated.rotated_at).toLocaleString('zh-CN')}
      </Text>
    </Space>
  );

  return (
    <Modal
      title={`轮换密码 — ${dataSource.name}`}
      open={open}
      onCancel={generated ? onClose : undefined}
      // No maskClosable after success — admin must explicitly close to
      // acknowledge they've saved the password.
      maskClosable={!generated}
      keyboard={!generated}
      footer={
        generated ? (
          <Button type="primary" onClick={onClose}>
            我已保存，关闭
          </Button>
        ) : (
          <Space>
            <Button onClick={onClose} disabled={pending}>
              取消
            </Button>
            <Button type="primary" onClick={handleSubmit} loading={pending}>
              {mode === 'admin_supplied' ? '确认轮换' : '生成新密码'}
            </Button>
          </Space>
        )
      }
      width={560}
      destroyOnClose
    >
      {generated ? (
        renderAfterSuccess
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="轮换后会立即清空 cached SQLAlchemy 引擎，并写入审计日志（data_source.password_rotated）。"
          />

          <Form form={form} layout="vertical">
            <Form.Item label="轮换方式">
              <Radio.Group
                value={mode}
                onChange={(e) => setMode(e.target.value as Mode)}
              >
                <Space direction="vertical">
                  <Radio value="admin_supplied">
                    我提供新密码（已知明文，如同步自运维 / 1Password）
                  </Radio>
                  <Radio value="server_generated">
                    服务器生成强随机密码（明文仅显示一次，关闭后不可再查）
                  </Radio>
                </Space>
              </Radio.Group>
            </Form.Item>

            {mode === 'admin_supplied' ? (
              <Form.Item
                name="new_password"
                label="新密码"
                rules={[
                  { required: true, message: '请输入新密码' },
                  { max: 255, message: '密码长度不能超过 255 个字符' },
                ]}
              >
                <Input.Password
                  placeholder="例如：与运维同步的新密码"
                  autoComplete="new-password"
                />
              </Form.Item>
            ) : (
              <Form.Item>
                <Checkbox
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                >
                  我理解新密码只会显示一次，关闭弹窗后无法再次查看
                </Checkbox>
              </Form.Item>
            )}
          </Form>
        </Space>
      )}
    </Modal>
  );
}
