/** Read-only dashboard view (批 14.3).
 *
 * Renders the grid in ``readOnly`` mode and shows the per-row
 * subscriptions panel from :component:`MySubscriptionsPage`. The
 * "订阅" button here opens :component:`DashboardSubscriptionModal`
 * (a sibling to the report-side :component:`SubscriptionModal`).
 *
 * ACL: the backend's ``GET /dashboards/{id}`` already returns 404 for
 * users without read access. We don't gatekeep again on the frontend —
 * the page renders whatever the server returned.
 */

import { useState } from 'react';
import { Button, Result, Space, Spin, Typography } from 'antd';
import { BellOutlined, EditOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { DashboardGridEditor } from '../components/DashboardGridEditor';
import { DashboardSubscriptionModal } from '../components/DashboardSubscriptionModal';
import { MySubscriptionsPanel } from '../components/MySubscriptionsPanel';
import { dashboardApi } from '../api';
import type { DashboardItem, DashboardItemLayoutEntry } from '../types';

const { Title, Text } = Typography;

export default function DashboardView() {
  const { id } = useParams<{ id: string }>();
  const dashboardId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [subOpen, setSubOpen] = useState(false);

  const dashboardQuery = useQuery({
    queryKey: ['dashboard', dashboardId],
    queryFn: () => dashboardApi.get(dashboardId),
    enabled: Number.isFinite(dashboardId),
  });

  // Reverse-link navigation (D 双向 link). report items land in the
  // editor (so the viewer can see items + config), chart items land
  // on the data-source list (no /data-sources/:id detail page exists
  // today — easy to upgrade by changing this one line).
  const handleOpenSource = (item: DashboardItem) => {
    if (item.item_type === 'report' && item.report_id != null) {
      navigate(`/reports/${item.report_id}`);
    } else if (item.item_type === 'chart' && item.data_source_id != null) {
      navigate('/data-sources');
    }
  };

  const itemsQuery = useQuery({
    queryKey: ['dashboard', dashboardId, 'items'],
    queryFn: () => dashboardApi.listItems(dashboardId),
    enabled: Number.isFinite(dashboardId),
  });

  // The read-only view intentionally doesn't PATCH on layout change —
  // any layout event here is a no-op. We pass a stub so the prop is
  // satisfied without enabling writes. The mutationFn accepts whatever
  // shape ``DashboardGridEditor`` forwards (LayoutEntry[]) but discards
  // it; ``void entries`` silences the unused-parameter lint without
  // forcing a second ``arguments`` overload.
  const layoutStub = useMutation({
    mutationFn: async (entries: DashboardItemLayoutEntry[]): Promise<void> => {
      void entries;
    },
  });

  const canEdit = dashboardQuery.data?.can_edit ?? false;

  if (!Number.isFinite(dashboardId)) {
    return (
      <Result status="404" title="看板不存在" extra={<Link to="/dashboards">返回列表</Link>} />
    );
  }

  if (dashboardQuery.isLoading || itemsQuery.isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    );
  }

  if (dashboardQuery.error) {
    // 404 from the backend (uniform not-found) lands here; surface a friendly
    // empty state rather than the raw axios error string.
    return (
      <Result
        status="404"
        title="看板不存在或您无权访问"
        extra={<Link to="/dashboards">返回列表</Link>}
      />
    );
  }

  const dashboard = dashboardQuery.data;
  if (!dashboard) return null;

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>
            {dashboard.name}
          </Title>
          {dashboard.description && (
            <Text type="secondary" style={{ fontSize: 13 }}>
              {dashboard.description}
            </Text>
          )}
        </div>
        <Space>
          <Button icon={<BellOutlined />} onClick={() => setSubOpen(true)} disabled={!canEdit}>
            订阅
          </Button>
          {canEdit && (
            <Button
              type="primary"
              icon={<EditOutlined />}
              onClick={() => navigate(`/dashboards/${dashboardId}/edit`)}
            >
              编辑
            </Button>
          )}
        </Space>
      </Space>

      <DashboardGridEditor
        items={itemsQuery.data ?? []}
        readOnly
        onLayoutChange={(entries) => layoutStub.mutate(entries)}
        onOpenSource={handleOpenSource}
      />

      {canEdit && (
        <div style={{ marginTop: 32 }}>
          <Title level={5}>我的看板订阅</Title>
          <MySubscriptionsPanel
            scope="dashboard"
            targetId={dashboardId}
            // Reuse the same cache so a successful subscription create
            // invalidates the list and re-fetches the panel.
            queryKey={['my-dashboard-subscriptions']}
            onChanged={() => {
              queryClient.invalidateQueries({ queryKey: ['my-dashboard-subscriptions'] });
            }}
          />
        </div>
      )}

      <DashboardSubscriptionModal
        open={subOpen}
        dashboard={dashboard}
        onClose={() => setSubOpen(false)}
      />
    </div>
  );
}
