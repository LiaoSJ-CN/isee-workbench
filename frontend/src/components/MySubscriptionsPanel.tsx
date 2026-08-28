/** Subscriptions panel reused across report + dashboard views (批 14.3).
 *
 * Slim companion to :component:`MySubscriptions` — same data shape
 * (a list of subscriptions + pause/resume/delete), but filtered to a
 * single target (one report OR one dashboard) and rendered as an
 * inline Card instead of a full page. Used by :component:`DashboardView`
 * to show the operator their subscriptions for *this* dashboard
 * without making them leave the page.
 *
 * The two scopes diverge only in:
 *   - the client (``subscriptionApi`` vs ``dashboardSubscriptionApi``)
 *   - the report/dashboard id field name
 *   - the target name (resolved via ``targetName`` prop to keep the
 *     panel agnostic about the source API)
 *
 * ``queryKey`` is passed by the parent so a successful create from
 * :component:`SubscriptionModal` / :component:`DashboardSubscriptionModal`
 * invalidates the same cache entry the panel reads from.
 */

import { useMemo } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { dashboardSubscriptionApi, subscriptionApi } from '../api';
import type { DashboardSubscription, ReportSubscription } from '../types';

const { Text } = Typography;

interface BaseProps {
  /** Backend cache key the panel reads + invalidates. Must match
   *  the key used by the create modal so the row appears immediately. */
  queryKey: readonly unknown[];
  /** Optional callback fired after pause / resume / delete so the parent
   *  can run any extra invalidation (e.g. cross-list dashboards). */
  onChanged?: () => void;
}

export interface ReportSubscriptionsPanelProps extends BaseProps {
  scope: 'report';
  reportId: number;
}

export interface DashboardSubscriptionsPanelProps extends BaseProps {
  scope: 'dashboard';
  targetId: number;
}

export type MySubscriptionsPanelProps =
  | ReportSubscriptionsPanelProps
  | DashboardSubscriptionsPanelProps;

interface NotificationConfigLike {
  type?: string;
  to?: string[];
  url?: string;
  webhook_url?: string;
}

function summarizeNotification(cfg: NotificationConfigLike | null | undefined): string {
  if (!cfg) return '— (no notification)';
  switch (cfg.type) {
    case 'email':
      return `email → ${(cfg.to ?? []).join(', ')}`;
    case 'webhook':
      return `webhook → ${cfg.url}`;
    case 'dingtalk':
    case 'feishu':
    case 'wechatwork':
      return `${cfg.type} → ${cfg.webhook_url}`;
    default:
      return 'unknown';
  }
}

function notificationTagColor(cfg: NotificationConfigLike | null | undefined): string {
  if (!cfg) return 'default';
  switch (cfg.type) {
    case 'email':
      return 'blue';
    case 'webhook':
      return 'purple';
    case 'dingtalk':
      return 'orange';
    case 'feishu':
      return 'cyan';
    case 'wechatwork':
      return 'green';
    default:
      return 'default';
  }
}

export function MySubscriptionsPanel(props: MySubscriptionsPanelProps) {
  const queryClient = useQueryClient();

  // ---- scope dispatch ----
  // The two scopes are kept in separate branches to keep the type
  // narrowing tight — ``DashboardSubscription`` and ``ReportSubscription``
  // share most fields but the union prevents accidentally passing one
  // to the other's client.
  const isReport = props.scope === 'report';

  const subsQuery = useQuery({
    queryKey: props.queryKey,
    queryFn: () =>
      isReport
        ? subscriptionApi.list(undefined, 100, 0)
        : dashboardSubscriptionApi.list(undefined, 100, 0),
  });

  const allSubs = useMemo(() => subsQuery.data ?? [], [subsQuery.data]);

  // Filter client-side to the target. The list endpoint returns
  // only the current user's subscriptions, and the row count is
  // small — a single pass is fine.
  const rows = useMemo(() => {
    if (isReport) {
      const rid = (props as ReportSubscriptionsPanelProps).reportId;
      return (allSubs as ReportSubscription[]).filter((s) => s.report_id === rid);
    }
    const did = (props as DashboardSubscriptionsPanelProps).targetId;
    return (allSubs as DashboardSubscription[]).filter((s) => s.dashboard_id === did);
  }, [allSubs, isReport, props]);

  // ---- mutations ----
  const pauseMut = useMutation({
    mutationFn: (id: number) =>
      isReport ? subscriptionApi.pause(id) : dashboardSubscriptionApi.pause(id),
    onSuccess: () => {
      message.success('订阅已暂停');
      queryClient.invalidateQueries({ queryKey: props.queryKey });
      props.onChanged?.();
    },
    onError: () => message.error('暂停失败'),
  });

  const resumeMut = useMutation({
    mutationFn: (id: number) =>
      isReport ? subscriptionApi.resume(id) : dashboardSubscriptionApi.resume(id),
    onSuccess: () => {
      message.success('订阅已恢复');
      queryClient.invalidateQueries({ queryKey: props.queryKey });
      props.onChanged?.();
    },
    onError: () => message.error('恢复失败'),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) =>
      isReport ? subscriptionApi.delete(id) : dashboardSubscriptionApi.delete(id),
    onSuccess: () => {
      message.success('订阅已删除');
      queryClient.invalidateQueries({ queryKey: props.queryKey });
      props.onChanged?.();
    },
    onError: () => message.error('删除失败'),
  });

  return (
    <Card size="small">
      {subsQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="加载订阅失败"
          description={(subsQuery.error as Error)?.message ?? '未知错误'}
        />
      ) : null}

      {subsQuery.isPending ? (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Spin />
        </div>
      ) : rows.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={isReport ? '该报表暂无订阅' : '该看板暂无订阅'}
        />
      ) : (
        <Table
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={rows as Array<{ id: number }>}
          columns={[
            {
              title: 'cron',
              dataIndex: 'cron_expression',
              render: (cron: string) => (
                <Tooltip title="6 字段 cron: 分 时 日 月 周 年">
                  <code style={{ fontSize: 12 }}>{cron}</code>
                </Tooltip>
              ),
            },
            {
              title: '通知',
              key: 'notification',
              render: (_: unknown, row: { notification_config?: NotificationConfigLike | null }) => {
                const cfg = row.notification_config ?? null;
                return (
                  <Space size={4}>
                    <Tag color={notificationTagColor(cfg)}>{cfg?.type ?? 'none'}</Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {summarizeNotification(cfg)}
                    </Text>
                  </Space>
                );
              },
            },
            {
              title: '状态',
              key: 'status',
              render: (_: unknown, row: { is_active: boolean }) =>
                row.is_active ? <Tag color="green">运行中</Tag> : <Tag color="default">已暂停</Tag>,
            },
            {
              title: '上次运行',
              dataIndex: 'last_run_at',
              render: (ts: string | null) => (ts ? new Date(ts).toLocaleString('zh-CN') : '—'),
            },
            {
              title: '操作',
              key: 'actions',
              render: (_: unknown, row: { id: number; is_active: boolean }) => (
                <Space size={4}>
                  {row.is_active ? (
                    <Button
                      type="link"
                      size="small"
                      icon={<PauseCircleOutlined />}
                      onClick={() => pauseMut.mutate(row.id)}
                      loading={pauseMut.isPending && pauseMut.variables === row.id}
                    >
                      暂停
                    </Button>
                  ) : (
                    <Button
                      type="link"
                      size="small"
                      icon={<PlayCircleOutlined />}
                      onClick={() => resumeMut.mutate(row.id)}
                      loading={resumeMut.isPending && resumeMut.variables === row.id}
                    >
                      恢复
                    </Button>
                  )}
                  <Popconfirm
                    title="确定要删除此订阅？"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => deleteMut.mutate(row.id)}
                  >
                    <Button
                      type="link"
                      size="small"
                      danger
                      loading={deleteMut.isPending && deleteMut.variables === row.id}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      )}
    </Card>
  );
}
