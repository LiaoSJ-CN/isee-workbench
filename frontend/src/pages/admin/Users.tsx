/**
 * Admin user-management page (批 user-management S3+S4).
 *
 * Top-level surface for the admin route ``/admin/users``. Surfaces:
 *
 *  - Paginated user table with role / disabled / username filters.
 *  - ``新建用户`` button → :component:`UserFormModal` (create mode).
 *  - ``+集中授权`` button → :component:`GrantModal` (no preset user).
 *  - Per-row actions: 重置密码 / 编辑 (opens drawer) / 禁用 or 启用.
 *  - Clickable username row → opens :component:`UserDetailDrawer` with
 *    four tabs (基本信息 / 数据源授权 / 报表授权 / 看板授权).
 *
 * Self-protection: the operator's own row disables both the inline
 * 禁用/启用 button and the corresponding affordance inside the
 * drawer footer (UserDetailDrawer applies the same rule from the
 * ``currentUserId`` prop). The backend enforces the same via 403.
 */

import { useState } from 'react';
import {
  Alert,
  Button,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
  CheckCircleOutlined,
  ShareAltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import {
  useAdminCreateUser,
  useAdminDisableUser,
  useAdminResetUserPassword,
  useAdminUpdateUser,
  useAdminUsers,
} from '../../queries/useAdminUsers';
import { useMe } from '../../queries/useAuth';
import { formatError } from '../../utils/error';
import type {
  AdminUserRole,
  PasswordResetRequest,
  PasswordResetResponse,
  UserResponse,
} from '../../types';
import { UserFormModal } from './UserFormModal';
import { UserDetailDrawer } from './UserDetailDrawer';
import { ResetPasswordModal } from './ResetPasswordModal';
import { GrantModal } from './GrantModal';

const { Text } = Typography;

const ROLE_TAG_COLOR: Record<AdminUserRole, string> = {
  admin: 'red',
  editor: 'blue',
  viewer: 'default',
};

const ROLE_LABEL: Record<AdminUserRole, string> = {
  admin: '管理员',
  editor: '编辑',
  viewer: '查看者',
};

const FILTER_ROLE_OPTIONS: { value: AdminUserRole | ''; label: string }[] = [
  { value: '', label: '全部角色' },
  { value: 'admin', label: '管理员' },
  { value: 'editor', label: '编辑' },
  { value: 'viewer', label: '查看者' },
];

const FILTER_DISABLED_OPTIONS: { value: '' | 'true' | 'false'; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'false', label: '启用' },
  { value: 'true', label: '禁用' },
];

export default function Users() {
  const me = useMe();
  const currentUserId = me.data?.user_id ?? null;

  // Filters — kept in component state, baked into the query key.
  const [roleFilter, setRoleFilter] = useState<AdminUserRole | ''>('');
  const [disabledFilter, setDisabledFilter] = useState<'' | 'true' | 'false'>('');
  const [qFilter, setQFilter] = useState('');

  const filters = {
    ...(roleFilter ? { role: roleFilter } : {}),
    ...(disabledFilter ? { disabled: disabledFilter === 'true' } : {}),
    ...(qFilter.trim() ? { q: qFilter.trim() } : {}),
  };

  const listQuery = useAdminUsers(filters);
  const createMut = useAdminCreateUser();
  const disableMut = useAdminDisableUser();
  const updateMut = useAdminUpdateUser();
  const resetMut = useAdminResetUserPassword();

  // Modal / drawer state
  const [formMode, setFormMode] = useState<'create' | 'edit' | null>(null);
  const [editingUser, setEditingUser] = useState<UserResponse | null>(null);
  const [drawerUser, setDrawerUser] = useState<UserResponse | null>(null);
  const [resetTarget, setResetTarget] = useState<UserResponse | null>(null);
  const [grantOpen, setGrantOpen] = useState(false);

  const handleDisableToggle = (user: UserResponse) => {
    disableMut.mutate(user.id, {
      onSuccess: (u) =>
        message.success(u.disabled ? `「${u.username}」已禁用` : `「${u.username}」已启用`),
      onError: (err: Error) => message.error(formatError(err, '操作失败')),
    });
  };

  const handleResetPassword = async (
    userId: number,
    payload: PasswordResetRequest,
  ): Promise<PasswordResetResponse> => {
    // Mutations return the response directly so the parent
    // (ResetPasswordModal) can decide what to do with the generated
    // plaintext. We intentionally do NOT toast success here — the
    // modal owns the "admin_supplied" success toast + auto-close, and
    // for "server_generated" it stays open to display the plaintext.
    return resetMut.mutateAsync({ id: userId, payload });
  };

  const openEdit = (user: UserResponse) => {
    setEditingUser(user);
    setFormMode('edit');
  };

  const openReset = (user: UserResponse) => {
    setResetTarget(user);
  };

  const columns: ColumnsType<UserResponse> = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (name: string, row) => (
        <Space direction="vertical" size={0}>
          <Button
            type="link"
            onClick={() => setDrawerUser(row)}
            style={{ padding: 0, height: 'auto' }}
            data-testid={`open-drawer-${row.id}`}
          >
            {name}
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            #{row.id}
            {row.id === currentUserId ? ' · 你' : ''}
          </Text>
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      render: (role: AdminUserRole) => (
        <Tag color={ROLE_TAG_COLOR[role]}>{ROLE_LABEL[role]}</Tag>
      ),
      filters: [
        { text: '管理员', value: 'admin' },
        { text: '编辑', value: 'editor' },
        { text: '查看者', value: 'viewer' },
      ],
      onFilter: () => true, // filter is applied server-side via `filters`
    },
    {
      title: '状态',
      dataIndex: 'disabled',
      key: 'disabled',
      render: (disabled: boolean) =>
        disabled ? (
          <Tag color="red" data-testid="tag-disabled">
            禁用
          </Tag>
        ) : (
          <Tag color="green" data-testid="tag-enabled">
            启用
          </Tag>
        ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (s?: string | null) => (s ? new Date(s).toLocaleString('zh-CN') : '—'),
    },
    {
      title: '最近登录',
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      render: (s?: string | null) => (s ? new Date(s).toLocaleString('zh-CN') : '从未登录'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_v, user) => {
        const isSelf = user.id === currentUserId;
        return (
          <Space>
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEdit(user)}
              data-testid={`edit-${user.id}`}
            >
              编辑
            </Button>
            <Button
              type="link"
              size="small"
              icon={<KeyOutlined />}
              onClick={() => openReset(user)}
              data-testid={`reset-${user.id}`}
            >
              重置密码
            </Button>
            <Popconfirm
              title={isSelf ? '您不能禁用自己' : user.disabled ? `确认启用「${user.username}」?` : `确认禁用「${user.username}」?`}
              okText={isSelf ? '好的' : user.disabled ? '启用' : '禁用'}
              cancelText="取消"
              okButtonProps={isSelf ? { disabled: true } : { danger: !user.disabled }}
              disabled={isSelf}
              onConfirm={isSelf ? undefined : () => handleDisableToggle(user)}
            >
              <Button
                type="link"
                size="small"
                danger={!user.disabled}
                icon={user.disabled ? <CheckCircleOutlined /> : <StopOutlined />}
                disabled={isSelf}
                loading={disableMut.isPending && disableMut.variables === user.id}
                data-testid={`toggle-${user.id}`}
              >
                {user.disabled ? '启用' : '禁用'}
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const items = listQuery.data?.items ?? [];
  const total = listQuery.data?.total ?? 0;

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* ---- toolbar ---- */}
      <Space wrap>
        <Select
          value={roleFilter}
          options={FILTER_ROLE_OPTIONS}
          onChange={(v) => setRoleFilter(v as AdminUserRole | '')}
          style={{ width: 140 }}
          data-testid="filter-role"
        />
        <Select
          value={disabledFilter}
          options={FILTER_DISABLED_OPTIONS}
          onChange={(v) => setDisabledFilter(v as '' | 'true' | 'false')}
          style={{ width: 140 }}
          data-testid="filter-disabled"
        />
        <Input.Search
          allowClear
          placeholder="按用户名 / 角色搜索"
          onSearch={(v) => setQFilter(v)}
          onChange={(e) => {
            // Empty input clears the filter immediately so the user
            // doesn't have to hit Enter.
            if (e.target.value === '') setQFilter('');
          }}
          style={{ width: 240 }}
          data-testid="filter-q"
        />
        <Button
          icon={<ReloadOutlined />}
          onClick={() => listQuery.refetch()}
          loading={listQuery.isFetching}
          data-testid="refresh-button"
        >
          刷新
        </Button>
        <Button
          type="primary"
          icon={<ShareAltOutlined />}
          onClick={() => setGrantOpen(true)}
          data-testid="open-grant-modal"
        >
          +集中授权
        </Button>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditingUser(null);
            setFormMode('create');
          }}
          data-testid="open-create-modal"
        >
          新建用户
        </Button>
      </Space>

      {currentUserId == null && (
        <Alert
          type="warning"
          showIcon
          message="未能识别当前登录用户 — 部分自保护逻辑(禁用自己)可能失效。"
        />
      )}

      <Table<UserResponse>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={listQuery.isPending}
        pagination={{
          total,
          pageSize: 50,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 名用户`,
        }}
        locale={{ emptyText: '暂无用户' }}
        data-testid="users-table"
      />

      <UserFormModal
        open={formMode != null}
        mode={formMode ?? 'create'}
        user={editingUser}
        currentUserId={currentUserId}
        pending={createMut.isPending || updateMut.isPending}
        onSubmit={async (payload) => {
          if (formMode === 'create') {
            return createMut.mutateAsync(payload as { username: string; password: string; role: AdminUserRole });
          }
          return updateMut.mutateAsync({
            id: editingUser!.id,
            payload: payload as { role?: AdminUserRole; disabled?: boolean },
          });
        }}
        onClose={() => {
          setFormMode(null);
          setEditingUser(null);
        }}
      />

      <UserDetailDrawer
        open={drawerUser != null}
        user={drawerUser}
        currentUserId={currentUserId}
        onResetPassword={handleResetPassword}
        onClose={() => setDrawerUser(null)}
      />

      <ResetPasswordModal
        open={resetTarget != null}
        user={resetTarget}
        pending={resetMut.isPending}
        onSubmit={async (payload) => {
          if (!resetTarget) throw new Error('no target');
          return handleResetPassword(resetTarget.id, payload);
        }}
        onClose={() => setResetTarget(null)}
      />

      <GrantModal open={grantOpen} onClose={() => setGrantOpen(false)} />
    </Space>
  );
}