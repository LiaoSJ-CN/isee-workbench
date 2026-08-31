/**
 * User detail drawer (批 user-management S3+S4).
 *
 * Opened from the Users page when the admin clicks "编辑" on a row.
 * Four tabs:
 *
 *   1. **基本信息** — read-only descriptions of id / username / role /
 *      disabled / created_at / last_login_at.
 *   2. **数据源授权** — table of grants filtered to
 *      ``resource_type='data_source'``. Top-right ``+授权`` button
 *      opens :component:`GrantModal` with
 *      ``defaultResourceType='data_source'`` (no preset user — admin
 *      picks the grantee inside the modal).
 *   3. **报表授权** — same shape, filtered to ``report``.
 *   4. **看板授权** — same shape, filtered to ``dashboard``.
 *
 * Footer: `重置密码` opens :component:`ResetPasswordModal`; `禁用` /
 * `启用` toggles via ``useAdminDisableUser`` + Popconfirm. Self-edit
 * disables both buttons (the backend enforces via 403 / no-op, but
 * disabling the button gives instant feedback).
 *
 * Cross-view invalidation: every grant mutation propagates to
 * ``adminUsers.all`` (so this drawer refreshes) and to the
 * per-resource ACL keys (so the matching DS / Report / Dashboard
 * share modals refresh too). ``useAdminGrant`` /
 * ``useAdminRevokeGrant`` own that cascade.
 */

import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Popconfirm,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  StopOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import type {
  AdminResourceType,
  GrantSummaryItem,
  PasswordResetRequest,
  PasswordResetResponse,
  UserResponse,
} from '../../types';
import {
  useAdminDisableUser,
  useAdminUpdateUser,
  useAdminUserGrants,
  useAdminRevokeGrant,
} from '../../queries/useAdminUsers';
import { ResetPasswordModal } from './ResetPasswordModal';
import { GrantModal } from './GrantModal';
import { formatError } from '../../utils/error';

const { Text } = Typography;

interface UserDetailDrawerProps {
  open: boolean;
  user: UserResponse | null;
  /** Operator's own id — used to disable self-edit affordances. */
  currentUserId: number | null | undefined;
  /** Reset-password mutation — injected from the parent so the page
   *  owns the React-Query state (toasts, cache invalidation, pending
   *  flag). The drawer just routes the click. */
  onResetPassword: (
    userId: number,
    payload: PasswordResetRequest,
  ) => Promise<PasswordResetResponse>;
  onClose: () => void;
}

const PERMISSION_TAG_COLOR: Record<string, string> = {
  read: 'blue',
  write: 'gold',
};

const RESOURCE_TAB_LABELS: Record<AdminResourceType, string> = {
  data_source: '数据源',
  report: '报表',
  dashboard: '看板',
};

/** Generic per-resource grants table — used inside each of the three
 *  resource tabs. Filter is applied client-side from the single
 *  ``useAdminUserGrants`` response. */
function GrantsTable({
  resourceType,
  grants,
  loading,
  onRevoke,
}: {
  resourceType: AdminResourceType;
  grants: GrantSummaryItem[] | undefined;
  loading: boolean;
  onRevoke: (g: GrantSummaryItem) => void;
}) {
  const filtered = useMemo(
    () => (grants ?? []).filter((g) => g.resource_type === resourceType),
    [grants, resourceType],
  );

  const columns: ColumnsType<GrantSummaryItem> = [
    {
      title: '资源',
      dataIndex: 'resource_name',
      key: 'resource',
      render: (name: string | null | undefined, row) =>
        name ? `${name} (#${row.resource_id})` : `#${row.resource_id}`,
    },
    {
      title: '权限',
      dataIndex: 'permission',
      key: 'permission',
      render: (p: string) => (
        <Tag color={PERMISSION_TAG_COLOR[p] ?? 'default'}>{p === 'write' ? '写' : '读'}</Tag>
      ),
    },
    {
      title: '授权人',
      dataIndex: 'granted_by_username',
      key: 'granted_by',
      render: (name?: string | null) => name ?? '—',
    },
    {
      title: '授权时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (s?: string | null) => (s ? new Date(s).toLocaleString('zh-CN') : '—'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_v, grant) => (
        <Popconfirm
          title="确认撤销该授权？"
          description={`该用户将立即失去对 ${RESOURCE_TAB_LABELS[resourceType]} #${grant.resource_id} 的访问权限。`}
          okText="撤销"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => onRevoke(grant)}
        >
          <Button
            type="link"
            size="small"
            danger
            data-testid={`drawer-revoke-${grant.grant_id}`}
          >
            撤销
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Table<GrantSummaryItem>
      size="small"
      rowKey="grant_id"
      columns={columns}
      dataSource={filtered}
      loading={loading}
      pagination={false}
      locale={{ emptyText: <Empty description={`暂无${RESOURCE_TAB_LABELS[resourceType]}授权`} /> }}
      data-testid={`drawer-grants-${resourceType}`}
    />
  );
}

export function UserDetailDrawer({
  open,
  user,
  currentUserId,
  onResetPassword,
  onClose,
}: UserDetailDrawerProps) {
  const grantsQuery = useAdminUserGrants(user?.id ?? null);
  const disableMut = useAdminDisableUser();
  const updateMut = useAdminUpdateUser();
  const revokeMut = useAdminRevokeGrant();

  const [resetOpen, setResetOpen] = useState(false);
  const [grantType, setGrantType] = useState<AdminResourceType | null>(null);

  const isSelf = user?.id === currentUserId;

  const handleDisableToggle = () => {
    if (!user) return;
    disableMut.mutate(user.id, {
      onSuccess: (u) => {
        message.success(u.disabled ? `「${u.username}」已禁用` : `「${u.username}」已启用`);
      },
      onError: (err: Error) => message.error(formatError(err, '操作失败')),
    });
  };

  const handleRevoke = (g: GrantSummaryItem) => {
    revokeMut.mutate(
      {
        resource_type: g.resource_type,
        grant_id: g.grant_id,
        resource_id: g.resource_id,
      },
      {
        onSuccess: () => message.success('已撤销'),
        onError: (err: Error) => message.error(formatError(err, '撤销失败')),
      },
    );
  };

  // The disabled-flag switch is a convenience for the admin when not
  // editing other fields. Mutates role+disabled together (no role
  // change when toggling — just disabled flips).
  const handleQuickDisabledToggle = (next: boolean) => {
    if (!user) return;
    updateMut.mutate(
      { id: user.id, payload: { role: user.role, disabled: next } },
      {
        onSuccess: (u) =>
          message.success(next ? `「${u.username}」已禁用` : `「${u.username}」已启用`),
        onError: (err: Error) => message.error(formatError(err, '操作失败')),
      },
    );
  };

  return (
    <>
      <Drawer
        title={
          user ? (
            <Space>
              <EditOutlined />
              <span>{user.username}</span>
              <Tag color={user.disabled ? 'red' : 'green'}>
                {user.disabled ? '已禁用' : '正常'}
              </Tag>
              <Tag color={user.role === 'admin' ? 'red' : user.role === 'editor' ? 'blue' : 'default'}>
                {user.role}
              </Tag>
            </Space>
          ) : (
            '用户详情'
          )
        }
        width={720}
        open={open}
        onClose={onClose}
        destroyOnClose
        extra={
          user ? (
            <Space>
              <Button
                icon={<KeyOutlined />}
                onClick={() => setResetOpen(true)}
                data-testid="drawer-reset-password"
              >
                重置密码
              </Button>
              <Popconfirm
                title={
                  isSelf
                    ? '您不能禁用自己'
                    : user.disabled
                      ? `确认启用「${user.username}」?`
                      : `确认禁用「${user.username}」?`
                }
                okText={isSelf ? '好的' : user.disabled ? '启用' : '禁用'}
                cancelText="取消"
                okButtonProps={isSelf ? { disabled: true } : { danger: !user.disabled }}
                disabled={isSelf}
                onConfirm={isSelf ? undefined : handleDisableToggle}
              >
                <Button
                  danger={!user.disabled}
                  icon={user.disabled ? <CheckCircleOutlined /> : <StopOutlined />}
                  disabled={isSelf}
                  loading={disableMut.isPending}
                  data-testid="drawer-disable"
                >
                  {user.disabled ? '启用' : '禁用'}
                </Button>
              </Popconfirm>
            </Space>
          ) : null
        }
      >
        {user && (
          <Tabs
            defaultActiveKey="info"
            data-testid="drawer-tabs"
            items={[
              {
                key: 'info',
                label: '基本信息',
                children: (
                  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                    <Descriptions column={1} bordered size="small">
                      <Descriptions.Item label="ID">{user.id}</Descriptions.Item>
                      <Descriptions.Item label="用户名">{user.username}</Descriptions.Item>
                      <Descriptions.Item label="角色">
                        <Tag
                          color={
                            user.role === 'admin'
                              ? 'red'
                              : user.role === 'editor'
                                ? 'blue'
                                : 'default'
                          }
                        >
                          {user.role}
                        </Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="状态">
                        <Tag color={user.disabled ? 'red' : 'green'}>
                          {user.disabled ? '已禁用' : '正常'}
                        </Tag>
                        {!isSelf && (
                          <Popconfirm
                            title={user.disabled ? '确认启用？' : '确认禁用？'}
                            okText={user.disabled ? '启用' : '禁用'}
                            cancelText="取消"
                            okButtonProps={{ danger: !user.disabled }}
                            onConfirm={() => handleQuickDisabledToggle(!user.disabled)}
                          >
                            <Button
                              type="link"
                              size="small"
                              loading={updateMut.isPending}
                            >
                              {user.disabled ? '一键启用' : '一键禁用'}
                            </Button>
                          </Popconfirm>
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="创建时间">
                        {user.created_at
                          ? new Date(user.created_at).toLocaleString('zh-CN')
                          : '—'}
                      </Descriptions.Item>
                      <Descriptions.Item label="最近登录">
                        {user.last_login_at
                          ? new Date(user.last_login_at).toLocaleString('zh-CN')
                          : '从未登录'}
                      </Descriptions.Item>
                    </Descriptions>
                    {isSelf && (
                      <Alert
                        type="info"
                        showIcon
                        message="您正在查看自己的账号 — 部分管理操作已禁用 (重置密码仍可用, 但禁用/启用被锁定)。"
                      />
                    )}
                  </Space>
                ),
              },
              {
                key: 'data_source',
                label: `数据源授权 (${
                  (grantsQuery.data?.grants ?? []).filter((g) => g.resource_type === 'data_source')
                    .length
                })`,
                children: (
                  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <Text type="secondary">
                        该用户被授权访问的数据源, 来自其他 owner/admin 通过 share modal 或集中授权下发。
                      </Text>
                      <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        onClick={() => setGrantType('data_source')}
                        data-testid="drawer-add-grant-ds"
                      >
                        授权
                      </Button>
                    </Space>
                    <GrantsTable
                      resourceType="data_source"
                      grants={grantsQuery.data?.grants}
                      loading={grantsQuery.isPending}
                      onRevoke={handleRevoke}
                    />
                  </Space>
                ),
              },
              {
                key: 'report',
                label: `报表授权 (${
                  (grantsQuery.data?.grants ?? []).filter((g) => g.resource_type === 'report').length
                })`,
                children: (
                  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <Text type="secondary">该用户被授权访问的报表。</Text>
                      <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        onClick={() => setGrantType('report')}
                        data-testid="drawer-add-grant-report"
                      >
                        授权
                      </Button>
                    </Space>
                    <GrantsTable
                      resourceType="report"
                      grants={grantsQuery.data?.grants}
                      loading={grantsQuery.isPending}
                      onRevoke={handleRevoke}
                    />
                  </Space>
                ),
              },
              {
                key: 'dashboard',
                label: `看板授权 (${
                  (grantsQuery.data?.grants ?? []).filter(
                    (g) => g.resource_type === 'dashboard',
                  ).length
                })`,
                children: (
                  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <Text type="secondary">该用户被授权访问的看板。</Text>
                      <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        onClick={() => setGrantType('dashboard')}
                        data-testid="drawer-add-grant-dashboard"
                      >
                        授权
                      </Button>
                    </Space>
                    <GrantsTable
                      resourceType="dashboard"
                      grants={grantsQuery.data?.grants}
                      loading={grantsQuery.isPending}
                      onRevoke={handleRevoke}
                    />
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Drawer>

      <ResetPasswordModal
        open={resetOpen}
        user={user}
        pending={false}
        onSubmit={async (payload) => {
          if (!user) throw new Error('no user');
          return onResetPassword(user.id, payload);
        }}
        onClose={() => setResetOpen(false)}
      />

      <GrantModal
        open={grantType != null}
        defaultUserId={user?.id ?? null}
        defaultResourceType={grantType ?? undefined}
        onClose={() => setGrantType(null)}
      />
    </>
  );
}