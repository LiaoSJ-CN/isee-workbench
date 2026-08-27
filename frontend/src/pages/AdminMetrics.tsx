/** Admin pool-metrics dashboard (批 12).

Three panels, top-to-bottom:

1. **Health summary cards** — green / yellow / red counts + fleet size,
   rendered as four Ant Design ``Statistic`` cards.
2. **Per-DataSource table** — one row per registered DataSource with
   live counters, an inline-SVG sparkline of checkouts over the last
   24h, and a coloured health badge.
3. **Manual refresh button** — explicit refetch, since the cache has a
   30 s ``staleTime``.

The chart is intentionally a tiny inline SVG sparkline (single
``<path>``). Adding ``chart.js`` for one chart would balloon the
bundle for a dashboard that sits behind an admin guard, and SVG is
trivially testable in ``happy-dom``.

Defence in depth: ``App.RequireAdmin`` hides the route from non-admins
and the backend ``GET /admin/metrics`` is itself gated by
``admin_required``. If a non-admin reaches this page, the 403 lands
in the error banner.
*/

import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import { useAdminMetrics } from '../queries/useAdminMetrics';
import { formatError } from '../utils/error';
import type { DataSourcePoolStats, Health, HistoryBucket } from '../types';

const { Text } = Typography;

// ---- health → colour mapping -------------------------------------------

const HEALTH_COLOR: Record<Health, string> = {
  green: 'success',
  yellow: 'warning',
  red: 'error',
};

const HEALTH_LABEL: Record<Health, string> = {
  green: '健康',
  yellow: '关注',
  red: '告警',
};

// ---- inline SVG sparkline ----------------------------------------------

interface SparklineProps {
  /** One bucket per 5 minutes over the last 24h. */
  buckets: HistoryBucket[];
  /** Pixel width / height for the inline SVG. */
  width?: number;
  height?: number;
}

/**
 * Tiny inline-SVG sparkline of checkouts over the 24h history.
 *
 * Renders nothing for empty / single-point series — the chart only
 * earns its complexity once we have at least two data points to
 * connect. The path is intentionally simple: M (x,y) L (x,y) … and
 * the axis ticks are implicit (left = oldest, right = newest).
 */
function Sparkline({ buckets, width = 160, height = 32 }: SparklineProps) {
  if (buckets.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        role="img"
        aria-label="无足够历史数据绘制折线"
        data-testid="sparkline-empty"
      >
        <text x={width / 2} y={height / 2} textAnchor="middle" fill="#bfbfbf" fontSize="10">
          无数据
        </text>
      </svg>
    );
  }

  const max = buckets.reduce((m, b) => Math.max(m, b.checkouts), 0) || 1;
  const stepX = width / (buckets.length - 1);
  // Reserve 2 px top/bottom padding so the line never sits on the edge.
  const padY = 2;
  const usableHeight = height - padY * 2;

  const points = buckets.map((b, i) => {
    const x = i * stepX;
    const y = padY + (1 - b.checkouts / max) * usableHeight;
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  });

  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={`过去 24 小时借出趋势：峰值 ${max} 次`}
      data-testid="sparkline"
    >
      <path d={points.join(' ')} fill="none" stroke="#1677ff" strokeWidth={1.5} />
    </svg>
  );
}

// ---- main page ----------------------------------------------------------

export default function AdminMetrics() {
  const { data, isPending, isError, error, refetch, isFetching } = useAdminMetrics();

  // Table columns defined inline so the column config stays close to
  // the render path (and the sparkline component above is in scope).
  const columns: ColumnsType<DataSourcePoolStats> = [
    {
      title: '数据源',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, row) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            #{row.data_source_id} · {row.db_type}
          </Text>
        </Space>
      ),
    },
    {
      title: '当前活跃',
      dataIndex: 'active',
      key: 'active',
      align: 'right',
      render: (active: number, row) => (
        <Text>
          {active} <Text type="secondary">/ {row.pool_size}</Text>
        </Text>
      ),
    },
    {
      title: '24h 借出',
      dataIndex: 'checkouts_total',
      key: 'checkouts_total',
      align: 'right',
    },
    {
      title: '24h 超时',
      dataIndex: 'timeouts_total',
      key: 'timeouts_total',
      align: 'right',
      render: (n: number) =>
        n > 0 ? <Text type="warning">{n}</Text> : <Text type="secondary">0</Text>,
    },
    {
      title: '平均持有',
      dataIndex: 'avg_held_ms',
      key: 'avg_held_ms',
      align: 'right',
      render: (ms: number) => `${ms.toFixed(0)} ms`,
    },
    {
      title: '24h 借出趋势',
      dataIndex: 'history',
      key: 'history',
      render: (history: HistoryBucket[]) => <Sparkline buckets={history} />,
    },
    {
      title: '健康度',
      dataIndex: 'health',
      key: 'health',
      align: 'center',
      render: (h: Health) => (
        <Tag color={HEALTH_COLOR[h]} data-testid={`health-tag-${h}`}>
          {HEALTH_LABEL[h]}
        </Tag>
      ),
    },
  ];

  // ---- empty / pending / error -----------------------------------------

  if (isPending) {
    return <Empty description="加载中…" />;
  }
  if (isError) {
    return (
      <Alert
        type="error"
        message="加载失败"
        description={formatError(error, '加载监控数据失败，请稍后重试')}
        showIcon
      />
    );
  }

  const pools = data?.pools ?? [];
  const summary = data?.health_summary ?? { green: 0, yellow: 0, red: 0, total: 0 };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* ----- summary cards ----- */}
      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="数据源总数" value={summary.total} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="健康"
              value={summary.green}
              valueStyle={{ color: '#52c41a' }}
              suffix={`/ ${summary.total}`}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="关注"
              value={summary.yellow}
              valueStyle={{ color: '#faad14' }}
              suffix={`/ ${summary.total}`}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="告警"
              value={summary.red}
              valueStyle={{ color: '#ff4d4f' }}
              suffix={`/ ${summary.total}`}
            />
          </Card>
        </Col>
      </Row>

      {/* ----- toolbar ----- */}
      <Space>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => refetch()}
          loading={isFetching}
          data-testid="refresh-button"
        >
          刷新
        </Button>
        <Text type="secondary" data-testid="generated-at">
          最近更新：{data?.generated_at ? new Date(data.generated_at).toLocaleString() : '—'}
        </Text>
      </Space>

      {/* ----- per-DS table ----- */}
      <Table<DataSourcePoolStats>
        rowKey="data_source_id"
        columns={columns}
        dataSource={pools}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无已注册的数据源" /> }}
        data-testid="pool-table"
      />
    </Space>
  );
}
