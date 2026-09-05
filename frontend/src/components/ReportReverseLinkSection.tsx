import { Card, Empty, List, Space, Spin, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { useReferencingDashboards } from '../queries/useReports';
import type { DashboardRef } from '../types';

const { Text } = Typography;

/**
 * Reverse-link inline section for ``ReportEditor`` (D 双向 link).
 *
 * Surfaces which dashboards have a ``DashboardItem`` referencing this
 * report. The server endpoint
 * (``GET /reports/{report_id}/dashboards``) already deduplicates by
 * ``Dashboard.id`` and applies ACL per dashboard, so the list the
 * user sees here is exactly what they'd find in ``/dashboards``
 * filtered by "uses this report".
 *
 * The component is intentionally a non-tab inline block — the
 * Plan/Scope decision was to "minimize layout change". Editors
 * already live in the tabbed config; a thin card right under the
 * tabs lets them see "this report is referenced by N dashboards"
 * without leaving the page.
 */
export interface ReportReverseLinkSectionProps {
  reportId: number;
}

function DashboardRow({ dashboard }: { dashboard: DashboardRef }) {
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
          <Text type="secondary">{dashboard.item_count} 项引用本报表</Text>
        )}
      </Space>
    </List.Item>
  );
}

export function ReportReverseLinkSection({ reportId }: ReportReverseLinkSectionProps) {
  const dashboardsQ = useReferencingDashboards(reportId);

  return (
    <Card
      size="small"
      title="被引用的看板"
      style={{ marginTop: 16 }}
      data-testid="report-reverse-link-section"
      extra={
        <Text type="secondary">
          {dashboardsQ.data?.length ?? 0} 个看板通过 DashboardItem 引用了本报表
        </Text>
      }
    >
      {dashboardsQ.isPending ? (
        <div style={{ padding: 16, textAlign: 'center' }}>
          <Spin size="small" />
        </div>
      ) : dashboardsQ.data && dashboardsQ.data.length > 0 ? (
        <List
          size="small"
          dataSource={dashboardsQ.data}
          rowKey={(d) => d.id}
          renderItem={(d) => <DashboardRow dashboard={d} />}
        />
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无看板引用"
          style={{ padding: '8px 0' }}
        />
      )}
    </Card>
  );
}