import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ClearOutlined,
  CopyOutlined,
  FilterOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';

import type { AuditLog, AuditLogFilters } from '../types';
import { useAuditLogs } from '../queries/useAuditLog';
import { useUsers } from '../queries/useDataSources';
import { AUDIT_ACTIONS, AUDIT_TARGET_TYPES } from '../constants/audit';
import { formatError } from '../utils/error';

const { RangePicker } = DatePicker;
const { Text } = Typography;

interface FilterFormShape {
  actor_user_id?: number;
  action?: string;
  target_type?: string;
  target_id?: number;
  request_id?: string;
  ip_address?: string;
  /** Two-element date range — split into `since` / `until` ISO strings
   *  on submit so we can serialize cleanly into the query string. */
  range?: [Dayjs, Dayjs];
}

const PAGE_SIZE = 20;

/**
 * Admin-only audit log table (批 9.6).
 *
 * The backend route (``GET /audit-logs``) is already gated by
 * ``admin_required`` and exposes five filter dimensions plus
 * ``limit`` / ``offset``. This page is purely a UI: it mirrors those
 * filters as a Form, owns the pagination state, and renders rows
 * newest-first with an expandable view of ``before`` / ``after`` JSON.
 *
 * Defence in depth: ``App.RequireAdmin`` hides the menu item and
 * route from non-admins, but the backend would 403 them anyway if
 * they typed the URL by hand.
 */
export default function AuditLogPage() {
  const [form] = Form.useForm<FilterFormShape>();
  // Committed filters — what the API actually queried. The Form
  // values are unsubmitted drafts; clicking "查询" promotes the
  // draft into `filters` and resets offset to 0.
  const [filters, setFilters] = useState<AuditLogFilters>({ limit: PAGE_SIZE, offset: 0 });
  const { data, isPending, isError, error, refetch } = useAuditLogs(filters);

  // Actor id → username map. Loaded once on mount; deferred if the
  // route 404s so we don't show an error toast the user can't act
  // on (the page still works with raw ids).
  const usersQuery = useUsers({ enabled: true });
  const userMap = useMemo(() => {
    const m = new Map<number, { username: string; role: string }>();
    (usersQuery.data ?? []).forEach((u) => m.set(u.id, { username: u.username, role: u.role }));
    return m;
  }, [usersQuery.data]);

  const handleSearch = (raw: FilterFormShape) => {
    const request_id = raw.request_id?.trim();
    const ip_address = raw.ip_address?.trim();
    const next: AuditLogFilters = {
      actor_user_id: raw.actor_user_id,
      action: raw.action,
      target_type: raw.target_type,
      target_id: raw.target_id,
      // Only include the new quick-filter keys when the user typed
      // something — keeps the filters object (and the react-query
      // cache key) sparse instead of carrying ``undefined`` for
      // every blank field. The api client strips undefined anyway,
      // so this is wire-identical.
      ...(request_id && { request_id }),
      ...(ip_address && { ip_address }),
      since: raw.range?.[0]?.toISOString(),
      until: raw.range?.[1]?.toISOString(),
      limit: PAGE_SIZE,
      offset: 0,
    };
    setFilters(next);
  };

  const handleReset = () => {
    form.resetFields();
    setFilters({ limit: PAGE_SIZE, offset: 0 });
  };

  const handleTableChange = (pag: { current?: number; pageSize?: number }) => {
    setFilters((prev) => ({
      ...prev,
      limit: pag.pageSize ?? PAGE_SIZE,
      offset: ((pag.current ?? 1) - 1) * (pag.pageSize ?? PAGE_SIZE),
    }));
  };

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success('已复制');
    } catch {
      message.error('复制失败');
    }
  };

  const columns: ColumnsType<AuditLog> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (iso: string) => (
        <Tooltip title={iso}>
          <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>
            {dayjs(iso).format('YYYY-MM-DD HH:mm:ss')}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: '操作者',
      dataIndex: 'actor_user_id',
      key: 'actor_user_id',
      width: 140,
      render: (id: number | null) => {
        if (id == null) return <Text type="secondary">(已删除)</Text>;
        const u = userMap.get(id);
        return (
          <Space size={4}>
            <Text>{u?.username ?? `#${id}`}</Text>
            {u?.role && (
              <Tag color={u.role === 'admin' ? 'red' : u.role === 'editor' ? 'blue' : 'default'}>
                {u.role}
              </Tag>
            )}
          </Space>
        );
      },
    },
    {
      title: '操作',
      dataIndex: 'action',
      key: 'action',
      width: 200,
      render: (action: string) => <Tag color="geekblue">{action}</Tag>,
    },
    {
      title: '对象',
      key: 'target',
      width: 220,
      render: (_, row) => (
        <Space size={4}>
          <Tag>{row.target_type}</Tag>
          {row.target_id != null && <Text type="secondary">#{row.target_id}</Text>}
        </Space>
      ),
    },
    {
      title: 'IP',
      dataIndex: 'ip_address',
      key: 'ip_address',
      width: 130,
      render: (ip: string | null) =>
        ip ? (
          <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{ip}</Text>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '请求 ID',
      dataIndex: 'request_id',
      key: 'request_id',
      width: 140,
      render: (rid: string | null) =>
        rid ? (
          <Tooltip title="复制请求 ID">
            <Button
              type="link"
              size="small"
              icon={<CopyOutlined />}
              onClick={() => void copyText(rid)}
              style={{ fontFamily: 'monospace', padding: 0 }}
            >
              {rid.slice(0, 8)}
            </Button>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>审计日志</h2>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void refetch()}>
            刷新
          </Button>
        </Space>
      </div>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Form
          form={form}
          layout="inline"
          onFinish={handleSearch}
          initialValues={{}}
        >
          {/* Filter form order mirrors the table column order top-to-bottom
              (时间 → 操作者 → 操作 → 对象 → IP → 请求 ID) so an admin can
              scan both in the same direction. P3-1 originally inserted
              ``请求 ID`` / ``IP`` *before* 时间范围 and in the reverse of
              the table; this realignment landed after the user review. */}
          <Form.Item name="range" label="时间范围" style={{ width: 320 }}>
            <RangePicker
              showTime={{ format: 'HH:mm' }}
              format="YYYY-MM-DD HH:mm"
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item name="actor_user_id" label="操作者 ID" style={{ width: 160 }}>
            <InputNumber
              placeholder="用户 ID"
              min={1}
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item name="action" label="操作" style={{ width: 220 }}>
            <Select
              allowClear
              showSearch
              placeholder="选择操作类型"
              options={AUDIT_ACTIONS.map((a) => ({ value: a, label: a }))}
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item name="target_type" label="对象类型" style={{ width: 200 }}>
            <Select
              allowClear
              showSearch
              placeholder="选择对象类型"
              options={AUDIT_TARGET_TYPES.map((t) => ({ value: t, label: t }))}
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item name="target_id" label="对象 ID" style={{ width: 160 }}>
            <InputNumber placeholder="对象 ID" min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="ip_address" label="客户端 IP" style={{ width: 180 }}>
            <Input placeholder="如 10.0.0.5" allowClear />
          </Form.Item>
          <Form.Item name="request_id" label="请求 ID" style={{ width: 200 }}>
            <Input placeholder="如 abc12345..." allowClear />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" icon={<SearchOutlined />}>
                查询
              </Button>
              <Button onClick={handleReset} icon={<ClearOutlined />}>
                重置
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      {isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="加载审计日志失败"
          description={formatError(error, '请稍后重试')}
        />
      )}

      <Table<AuditLog>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey="id"
        loading={isPending}
        size="small"
        scroll={{ x: 'max-content' }}
        pagination={{
          current: (filters.offset ?? 0) / (filters.limit ?? PAGE_SIZE) + 1,
          pageSize: filters.limit ?? PAGE_SIZE,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
        }}
        onChange={handleTableChange}
        expandable={{
          expandedRowRender: (row) => (
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              <Card size="small" title="变更前 (before)" style={{ flex: 1, minWidth: 320 }}>
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {row.before ? JSON.stringify(row.before, null, 2) : '(无)'}
                </pre>
              </Card>
              <Card size="small" title="变更后 (after)" style={{ flex: 1, minWidth: 320 }}>
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {row.after ? JSON.stringify(row.after, null, 2) : '(无)'}
                </pre>
              </Card>
            </div>
          ),
        }}
        locale={{ emptyText: '暂无审计日志' }}
      />

      {/* Surface a "filters applied" hint so the user doesn't think the
          trimmed row count is suspicious. Excludes pagination state
          (limit/offset) which always carries a value. */}
      {[
        filters.actor_user_id,
        filters.action,
        filters.target_type,
        filters.target_id,
        filters.request_id,
        filters.ip_address,
        filters.since,
        filters.until,
      ].some((v) => v !== undefined && v !== '') && (
        <div style={{ marginTop: 12, color: '#666', fontSize: 12 }}>
          <FilterOutlined /> 已应用过滤条件
        </div>
      )}
    </div>
  );
}
