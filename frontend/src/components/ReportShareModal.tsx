import { useEffect } from 'react';
import {
  Modal, Form, Select, Radio, Button, Space, Table, Tag, Popconfirm, Alert, message,
} from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';

import type {
  Report, ReportShare, ReportSharePermission,
} from '../types';
import type { UserSummary } from '../api';

export interface ReportShareModalProps {
  visible: boolean;
  report: Report | null;
  shares: ReportShare[] | undefined;
  sharesLoading: boolean;
  users: UserSummary[] | undefined;
  usersLoading: boolean;
  /** Submit (POST) handler for a new share row. */
  onCreate: (payload: { user_id: number; permission: ReportSharePermission }) => void;
  /** Submit (DELETE) handler for an existing share row. */
  onRevoke: (share: ReportShare) => void;
  onCancel: () => void;
  createPending: boolean;
  revokePending: boolean;
}

interface AddShareForm {
  user_id: number;
  permission: ReportSharePermission;
}

/**
 * Per-report share editor (批 9.4).
 *
 * Mirrors :component:`DataSourceShareModal` — same form layout, same
 * "user dropdown → manual id" fallback. The backend enforces
 * owner-or-admin on the report itself; a successful ``listShares``
 * response means the caller already passed that check, so we can
 * render without re-checking client-side.
 *
 * Upsert semantics: posting the same (report_id, user_id) twice
 * overwrites the permission rather than failing the unique
 * constraint. The Table row key is therefore ``user_id`` (stable
 * across re-grants) — the ``id`` column is only used to DELETE.
 */
export function ReportShareModal({
  visible, report, shares, sharesLoading, users, usersLoading,
  onCreate, onRevoke, onCancel, createPending, revokePending,
}: ReportShareModalProps) {
  const [form] = Form.useForm<AddShareForm>();

  // Reset whenever the modal closes so reopening starts clean.
  useEffect(() => {
    if (!visible) form.resetFields();
  }, [visible, form]);

  const handleAdd = async () => {
    try {
      const values = await form.validateFields();
      onCreate(values);
      form.resetFields();
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    }
  };

  const userOptions = (users ?? []).map((u) => ({
    value: u.id,
    label: `${u.username} (${u.role})`,
  }));

  return (
    <Modal
      title={report ? `分享报表：${report.name}` : '分享报表'}
      open={visible}
      onCancel={onCancel}
      footer={null}
      width={560}
      destroyOnClose
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        {report?.visibility === 'public' && (
          <Alert
            type="info"
            showIcon
            message="当前为公开报表"
            description="任何人已可读；显式授权仅在需要把只读升级为写权限时才有意义。"
          />
        )}

        <Form
          form={form}
          layout="inline"
          initialValues={{ permission: 'read' }}
          onFinish={handleAdd}
        >
          <Form.Item
            name="user_id"
            label="用户"
            rules={[{ required: true, message: '请选择用户' }]}
            style={{ flex: 1, minWidth: 220 }}
          >
            {usersLoading ? (
              <Select placeholder="加载中..." loading disabled style={{ width: '100%' }} />
            ) : userOptions.length > 0 ? (
              <Select
                placeholder="选择用户"
                options={userOptions}
                showSearch
                optionFilterProp="label"
                style={{ width: '100%' }}
              />
            ) : (
              <Select
                placeholder="暂无可选用户"
                disabled
                style={{ width: '100%' }}
              />
            )}
          </Form.Item>

          <Form.Item name="permission" label="权限">
            <Radio.Group>
              <Radio.Button value="read">只读</Radio.Button>
              <Radio.Button value="write">读写</Radio.Button>
            </Radio.Group>
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<PlusOutlined />}
              loading={createPending}
            >
              添加
            </Button>
          </Form.Item>
        </Form>

        {users && users.length === 0 && (
          <Alert
            type="info"
            showIcon
            message="用户列表为空"
            description="目前没有可选的授权目标。"
          />
        )}

        <Table<ReportShare>
          rowKey="id"
          size="small"
          loading={sharesLoading}
          dataSource={shares ?? []}
          pagination={false}
          locale={{ emptyText: '尚未授权给任何用户' }}
          columns={[
            {
              title: '用户 ID',
              dataIndex: 'user_id',
              key: 'user_id',
              width: 100,
            },
            {
              title: '权限',
              dataIndex: 'permission',
              key: 'permission',
              width: 100,
              render: (p: string) => (
                <Tag color={p === 'write' ? 'geekblue' : 'default'}>
                  {p === 'write' ? '读写' : '只读'}
                </Tag>
              ),
            },
            {
              title: '授权人',
              dataIndex: 'granted_by',
              key: 'granted_by',
              width: 100,
              render: (v: number | null | undefined) => v ?? '-',
            },
            {
              title: '操作',
              key: 'action',
              width: 80,
              render: (_, share) => (
                <Popconfirm
                  title="确定撤销该授权?"
                  onConfirm={() => onRevoke(share)}
                  okText="撤销"
                  cancelText="取消"
                >
                  <Button
                    type="link"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    loading={revokePending}
                  >
                    撤销
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Space>
    </Modal>
  );
}
