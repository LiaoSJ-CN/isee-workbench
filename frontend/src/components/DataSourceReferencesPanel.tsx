import { Empty, List, Space, Spin, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { useReferencingReports, useReferencingDashboards } from '../queries/useDataSources';
import type { DashboardRef, ReportRef } from '../types';

const { Text } = Typography;

/**
 * Reverse-link panel for a single DataSource row (D 双向 link).
 *
 * Rendered inside ``DataSourceList``'s expandable row. Two listings:
 *
 *  - 引用的报表 — reports whose ``data_source_id`` points at this DS.
 *    Comes from ``GET /data-sources/{id}/reports`` and runs through
 *    ``list_accessible_reports`` so per-report ACL is applied.
 *  - 引用的看板 — dashboards that touch this DS directly (chart item)
 *    or transitively (report item whose ``report.data_source_id`` is
 *    this DS). Deduped server-side by ``Dashboard.id``.
 *
 * Both queries are gated on the parent being open — react-query's
 * ``enabled: id != null`` keeps them cold until the row is mounted
 * by the expander, and ``retry: false`` prevents the ACL 404
 * retry loop. An empty list means either "no usage" or "no ACL".
 * Both look the same to the user; that's intentional — they can't tell
 * whether a private DS exists by listing dashboards it touches.
 */
export interface DataSourceReferencesPanelProps {
  dataSourceId: number;
}

function ReportItemRow({ report }: { report: ReportRef }) {
  return (
    <List.Item>
      <Space>
        <Link to={`/reports/${report.id}`}>{report.name}</Link>
        <Tag color={report.visibility === 'private' ? 'default' : 'blue'}>
          {report.visibility === 'private'
            ? '私有'
            : report.visibility === 'org'
              ? '同部门'
              : '公开'}
        </Tag>
        {report.is_active === false && <Tag color="orange">已停用</Tag>}
      </Space>
    </List.Item>
  );
}

function DashboardItemRow({ dashboard }: { dashboard: DashboardRef }) {
  return (
    <List.Item>
      <Space>
        <Link to={`/dashboards/${dashboard.id}`}>{dashboard.name}</Link>
        <Tag color={dashboard.visibility === 'private' ? 'default' : 'blue'}>
          {dashboard.visibility === 'private'
            ? '私有'
            : dashboard.visibility === 'org'
              ? '同部门'
              : '公开'}
        </Tag>
        {typeof dashboard.item_count === 'number' && (
          <Text type="secondary">{dashboard.item_count} 项</Text>
        )}
      </Space>
    </List.Item>
  );
}

export function DataSourceReferencesPanel({ dataSourceId }: DataSourceReferencesPanelProps) {
  const reportsQ = useReferencingReports(dataSourceId);
  const dashboardsQ = useReferencingDashboards(dataSourceId);

  return (
    <div data-testid="ds-references-panel" style={{ padding: '4px 0' }}>
      <Space size="large" align="start" style={{ width: '100%' }} wrap>
        <div style={{ minWidth: 280, flex: 1 }}>
          <Text strong>引用的报表 ({reportsQ.data?.length ?? 0})</Text>
          {reportsQ.isPending ? (
            <div style={{ padding: 16, textAlign: 'center' }}>
              <Spin size="small" />
            </div>
          ) : reportsQ.data && reportsQ.data.length > 0 ? (
            <List
              size="small"
              dataSource={reportsQ.data}
              rowKey={(r) => r.id}
              renderItem={(r) => <ReportItemRow report={r} />}
            />
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无报表引用"
              style={{ padding: '8px 0' }}
            />
          )}
        </div>

        <div style={{ minWidth: 280, flex: 1 }}>
          <Text strong>引用的看板 ({dashboardsQ.data?.length ?? 0})</Text>
          {dashboardsQ.isPending ? (
            <div style={{ padding: 16, textAlign: 'center' }}>
              <Spin size="small" />
            </div>
          ) : dashboardsQ.data && dashboardsQ.data.length > 0 ? (
            <List
              size="small"
              dataSource={dashboardsQ.data}
              rowKey={(d) => d.id}
              renderItem={(d) => <DashboardItemRow dashboard={d} />}
            />
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无看板引用"
              style={{ padding: '8px 0' }}
            />
          )}
        </div>
      </Space>
    </div>
  );
}