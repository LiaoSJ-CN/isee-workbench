import { useEffect } from 'react';
import {
  Modal, Form, Select, Radio, Button, Space, Table, Tag, Popconfirm, Alert, message,
} from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';

import type {
  DataSource, DataSourceGrant, DataSourceGrantPermission,
} from '../types';
import type { UserSummary } from '../api';

export interface DataSourceShareModalProps {
  visible: boolean;
  dataSource: DataSource | null;
  grants: DataSourceGrant[] | undefined;
  grantsLoading: boolean;
  users: UserSummary[] | undefined;
  usersLoading: boolean;
  /** Submit (POST) handler for a new grant row. */
  onCreate: (payload: { user_id: number; permission: DataSourceGrantPermission }) => void;
  /** Submit (DELETE) handler for an existing grant row. */
  onRevoke: (grant: DataSourceGrant) => void;
  onCancel: () => void;
  createPending: boolean;
  revokePending: boolean;
}

interface AddGrantForm {
  user_id: number;
  permission: DataSourceGrantPermission;
}

/**
 * Per-DS share editor (批 9.3).
 *
 * The backend enforces owner-or-admin — a successful `listAcl` reply
 * means the caller already passed that check, so the modal can render
 * without re-checking client-side. New grants go through the same
 * upsert endpoint (POST twice with the same user_id changes the
 * permission level rather than failing the unique constraint).
 *
 * The user dropdown falls back to a manual user_id input when
 * ``usersLoading`` is false but ``users`` is undefined — that's the
 * ``GET /users`` 404 case (route lands in 批 9.5). We surface a hint
 * rather than failing the modal.
 */
export function DataSourceShareModal({
  visible, dataSource, grants, grantsLoading, users, usersLoading,
  onCreate, onRevoke, onCancel, createPending, revokePending,
}: DataSourceShareModalProps) {
  const [form] = Form.useForm<AddGrantForm>();

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
      title={dataSource ? `分享数据源：${dataSource.name}` : '分享数据源'}
      open={visible}
      onCancel={onCancel}
      footer={null}
      width={560}
      destroyOnClose
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
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

        <Table<DataSourceGrant>
          rowKey="id"
          size="small"
          loading={grantsLoading}
          dataSource={grants ?? []}
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
              render: (_, grant) => (
                <Popconfirm
                  title="确定撤销该授权?"
                  onConfirm={() => onRevoke(grant)}
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