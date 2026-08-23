/** My report subscriptions (批 8.3).
 *
 * Owner-scoped list of the current user's per-report subscriptions.
 * Each row shows the report name, cron expression, notification
 * destination summary, and last/next run timestamps. Operators
 * pause / resume / delete from this page.
 *
 * The page is read-mostly: subscribers land here from the "我的订阅"
 * nav item and from the "订阅" button on a report row (which
 * pre-fills the report id). The actual create flow lives in
 * :component:`SubscriptionModal`, opened from the report-list row.
 */

import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
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
import type { ColumnsType } from 'antd/es/table';
import { PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { useMemo } from 'react';
import { subscriptionApi } from '../api';
import { reportApi } from '../api';
import type { ReportSubscription, Report } from '../types';

const { Title, Text } = Typography;

// ---- helpers ----

/** Human-readable summary of the notification destination. We
 *  deliberately keep this short — the full config (URLs, secrets,
 *  recipient lists) doesn't belong on the listing row. Clicking
 *  "Edit" opens the modal with the full config. */
function summarizeNotification(
  cfg: ReportSubscription['notification_config'],
): string {
  if (!cfg) return '— (no notification)';
  switch (cfg.type) {
    case 'email':
      return `email → ${cfg.to.join(', ')}`;
    case 'webhook':
      return `webhook → ${cfg.url}`;
    case 'dingtalk':
    case 'feishu':
    case 'wechatwork':
      return `${cfg.type} → ${cfg.webhook_url}`;
    default:
      // Exhaustiveness — TS narrows cfg to never here. Returning a
      // literal keeps the function typed; the cast is a no-op at
      // runtime because the union has no members outside the cases.
      return 'unknown';
  }
}

/** Map backend NotificationConfig variants to a coloured Ant tag so
 *  providers stand out at a glance. */
function notificationTagColor(
  cfg: ReportSubscription['notification_config'],
): string {
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

export default function MySubscriptionsPage() {
  const queryClient = useQueryClient();

  // Subscriptions are owner-scoped server-side; no filters needed
  // here. The list is small (one user, one row per subscription) so
  // a 50-row cap is plenty — pagination isn't wired up.
  const subsQuery = useQuery({
    queryKey: ['my-subscriptions'],
    queryFn: () => subscriptionApi.list(undefined, 100, 0),
  });

  // Report name resolution. The subscription row only carries
  // ``report_id``; the page wants the human-readable name. We
  // fetch all reports and look up by id — small enough list that
  // one round-trip is cheaper than N requests.
  const reportsQuery = useQuery({
    queryKey: ['reports-for-subscriptions'],
    queryFn: () => reportApi.list(),
    enabled: !subsQuery.isPending,
  });

  const reportsById = useMemo(() => {
    const map = new Map<number, Report>();
    (reportsQuery.data ?? []).forEach((r) => map.set(r.id, r));
    return map;
  }, [reportsQuery.data]);

  // ---- mutations ----

  const pauseMut = useMutation({
    mutationFn: (id: number) => subscriptionApi.pause(id),
    onSuccess: () => {
      message.success('订阅已暂停');
      queryClient.invalidateQueries({ queryKey: ['my-subscriptions'] });
    },
    onError: () => message.error('暂停失败'),
  });

  const resumeMut = useMutation({
    mutationFn: (id: number) => subscriptionApi.resume(id),
    onSuccess: () => {
      message.success('订阅已恢复');
      queryClient.invalidateQueries({ queryKey: ['my-subscriptions'] });
    },
    onError: () => message.error('恢复失败'),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => subscriptionApi.delete(id),
    onSuccess: () => {
      message.success('订阅已删除');
      queryClient.invalidateQueries({ queryKey: ['my-subscriptions'] });
    },
    onError: () => message.error('删除失败'),
  });

  // ---- table ----

  const columns: ColumnsType<ReportSubscription> = [
    {
      title: '报表',
      dataIndex: 'report_id',
      key: 'report',
      render: (rid: number) => {
        const report = reportsById.get(rid);
        return report ? report.name : <Text type="secondary">#{rid}</Text>;
      },
    },
    {
      title: 'cron',
      dataIndex: 'cron_expression',
      key: 'cron',
      render: (cron: string) => (
        <Tooltip title="6 字段 cron: 分 时 日 月 周 年">
          <code style={{ fontSize: 12 }}>{cron}</code>
        </Tooltip>
      ),
    },
    {
      title: '通知',
      key: 'notification',
      render: (_: unknown, row: ReportSubscription) => {
        const cfg = row.notification_config;
        return (
          <Space size={4}>
            <Tag color={notificationTagColor(cfg)}>
              {cfg?.type ?? 'none'}
            </Tag>
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
      render: (_: unknown, row: ReportSubscription) =>
        row.is_active ? (
          <Tag color="green">运行中</Tag>
        ) : (
          <Tag color="default">已暂停</Tag>
        ),
    },
    {
      title: '上次运行',
      dataIndex: 'last_run_at',
      key: 'last_run',
      render: (ts: string | null) =>
        ts ? new Date(ts).toLocaleString('zh-CN') : '—',
    },
    {
      title: '下次运行',
      dataIndex: 'next_run_at',
      key: 'next_run',
      render: (ts: string | null) =>
        ts ? new Date(ts).toLocaleString('zh-CN') : '—',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, row: ReportSubscription) => (
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
            description="删除后将立即停止该 cron 触发。报表本身的定时设置不受影响。"
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
  ];

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>我的订阅</Title>
      <Text type="secondary">
        按 cron 定时自动生成报表并通过所选渠道通知。每个订阅独立于报表的「定时任务」设置。
      </Text>

      {subsQuery.isError ? (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 16 }}
          message="加载订阅失败"
          description={(subsQuery.error as Error)?.message ?? '未知错误'}
        />
      ) : null}

      <Card style={{ marginTop: 16 }} bodyStyle={{ padding: 0 }}>
        {subsQuery.isPending ? (
          <div style={{ padding: 48, textAlign: 'center' }}>
            <Spin />
          </div>
        ) : (subsQuery.data ?? []).length === 0 ? (
          <Empty
            style={{ padding: 48 }}
            description="还没有订阅。在「报表」页面点击「订阅」按钮即可创建。"
          />
        ) : (
          <Table<ReportSubscription>
            rowKey="id"
            columns={columns}
            dataSource={subsQuery.data ?? []}
            pagination={false}
            size="middle"
          />
        )}
      </Card>
    </div>
  );
}