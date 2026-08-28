/** Dashboard share editor (批 14).
 *
 * Mirrors :component:`ReportShareModal` — same form layout, same
 * "user dropdown → manual id" fallback. Upsert semantics: posting
 * the same ``(dashboard_id, user_id)`` twice overwrites the
 * permission level rather than failing the unique constraint, so
 * we key the table by ``user_id`` (stable across re-grants).
 *
 * The backend enforces owner-or-admin on the dashboard itself;
 * a successful ``listShares`` response means the caller already
 * passed that check.
 */

import { useEffect } from 'react';
import {
  Alert,
  Button,
  Form,
  message,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tag,
} from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';

import type {
  Dashboard,
  DashboardShare,
  DashboardSharePermission,
  UserSummary,
} from '../types';

export interface DashboardShareModalProps {
  visible: boolean;
  dashboard: Dashboard | null;
  shares: DashboardShare[] | undefined;
  sharesLoading: boolean;
  users: UserSummary[] | undefined;
  usersLoading: boolean;
  /** Submit (POST) handler for a new share row. */
  onCreate: (payload: { user_id: number; permission: DashboardSharePermission }) => void;
  /** Submit (DELETE) handler for an existing share row by user_id. */
  onRevoke: (userId: number) => void;
  onCancel: () => void;
  createPending: boolean;
  revokePending: boolean;
}

interface AddShareForm {
  user_id: number;
  permission: DashboardSharePermission;
}

export function DashboardShareModal({
  visible,
  dashboard,
  shares,
  sharesLoading,
  users,
  usersLoading,
  onCreate,
  onRevoke,
  onCancel,
  createPending,
  revokePending,
}: DashboardShareModalProps) {
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
      title={dashboard ? `分享看板：${dashboard.name}` : '分享看板'}
      open={visible}
      onCancel={onCancel}
      footer={null}
      width={560}
      destroyOnClose
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        {dashboard?.visibility === 'public' && (
          <Alert
            type="info"
            showIcon
            message="看板已设为公开；分享用于给特定用户写权限（public 本身只给读）。"
          />
        )}
        <Form<AddShareForm>
          form={form}
          layout="inline"
          initialValues={{ permission: 'read' }}
          style={{ rowGap: 8 }}
        >
          <Form.Item
            name="user_id"
            rules={[{ required: true, message: '请选择用户' }]}
            style={{ minWidth: 200 }}
          >
            <Select
              placeholder="选择用户"
              options={userOptions}
              loading={usersLoading}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="permission" rules={[{ required: true }]}>
            <Radio.Group>
              <Radio.Button value="read">读</Radio.Button>
              <Radio.Button value="write">写</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleAdd}
              loading={createPending}
            >
              添加
            </Button>
          </Form.Item>
        </Form>

        <Table<DashboardShare>
          rowKey="user_id"
          size="small"
          loading={sharesLoading}
          dataSource={shares ?? []}
          pagination={false}
          locale={{ emptyText: '尚未分享给任何用户' }}
          columns={[
            {
              title: '用户',
              dataIndex: 'user_id',
              render: (uid: number) => {
                const u = (users ?? []).find((x) => x.id === uid);
                return u ? `${u.username} (${u.role})` : `#${uid}`;
              },
            },
            {
              title: '权限',
              dataIndex: 'permission',
              width: 100,
              render: (p: DashboardSharePermission) =>
                p === 'write' ? <Tag color="orange">写</Tag> : <Tag>读</Tag>,
            },
            {
              title: '操作',
              width: 80,
              render: (_: unknown, row: DashboardShare) => (
                <Popconfirm
                  title="确认撤销该用户的看板访问？"
                  onConfirm={() => onRevoke(row.user_id)}
                  okText="撤销"
                  cancelText="取消"
                >
                  <Button
                    type="link"
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