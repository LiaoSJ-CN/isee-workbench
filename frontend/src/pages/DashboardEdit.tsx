/** Dashboard editor page (批 14.3).
 *
 * The page is intentionally narrow — it owns:
 *   - dashboard metadata form (name / description / visibility)
 *   - the drag-resize grid via :component:`DashboardGridEditor`
 *   - the item CRUD modal (:component:`DashboardItemEditorModal`)
 *   - the share modal (:component:`DashboardShareModal`)
 *
 * The grid debounces layout changes into one ``PATCH /dashboards/{id}/layout``
 * round-trip per drag/resize gesture. Item-level changes go through
 * individual create / update / delete endpoints so the audit log
 * has per-row fidelity.
 *
 * z-index risk note (per batch 14 plan Risk #1): react-grid-layout sets
 * a high z-index on the dragging element. AntD Modal default zIndex is
 * 1000, which already beats the grid's 100. We don't override either —
 * this worked in the 14.2 preview iframe and applies the same here.
 */

import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Result, Select, Space, Spin, Typography, message } from 'antd';
import { EyeOutlined, PlusOutlined, SaveOutlined, ShareAltOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { DashboardGridEditor } from '../components/DashboardGridEditor';
import {
  DashboardItemEditorModal,
  type DashboardItemFormValues,
} from '../components/DashboardItemEditorModal';
import { DashboardShareModal } from '../components/DashboardShareModal';
import { dashboardApi, dataSourceApi, reportApi, usersApi } from '../api';
import type {
  Dashboard,
  DashboardItem,
  DashboardItemLayoutEntry,
  DashboardShare,
  DashboardSharePermission,
  DashboardVisibility,
  UserSummary,
} from '../types';

const { Title, Text } = Typography;

interface DashboardMetaForm {
  name: string;
  description?: string | null;
  visibility: DashboardVisibility;
}

export default function DashboardEdit() {
  const { id } = useParams<{ id: string }>();
  const dashboardId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [metaForm] = Form.useForm<DashboardMetaForm>();

  const [editingItem, setEditingItem] = useState<DashboardItem | null>(null);
  const [creatingItem, setCreatingItem] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  // Reverse-link navigation (D 双向 link). Same dispatch as
  // ``DashboardView`` — see comment there for why we land chart
  // items on the DS list rather than a (currently nonexistent)
  // DS detail page.
  const handleOpenSource = (item: DashboardItem) => {
    if (item.item_type === 'report' && item.report_id != null) {
      navigate(`/reports/${item.report_id}`);
    } else if (item.item_type === 'chart' && item.data_source_id != null) {
      navigate('/data-sources');
    }
  };

  // ---- queries ----

  const dashboardQuery = useQuery({
    queryKey: ['dashboard', dashboardId],
    queryFn: () => dashboardApi.get(dashboardId),
    enabled: Number.isFinite(dashboardId),
  });

  const itemsQuery = useQuery({
    queryKey: ['dashboard', dashboardId, 'items'],
    queryFn: () => dashboardApi.listItems(dashboardId),
    enabled: Number.isFinite(dashboardId),
  });

  const sharesQuery = useQuery({
    queryKey: ['dashboard', dashboardId, 'shares'],
    queryFn: () => dashboardApi.listShares(dashboardId),
    enabled: Number.isFinite(dashboardId),
  });

  const usersQuery = useQuery<UserSummary[]>({
    queryKey: ['users-active'],
    queryFn: () => usersApi.list(),
  });

  const dsQuery = useQuery({
    queryKey: ['data-sources'],
    queryFn: () => dataSourceApi.list(),
  });

  const reportsQuery = useQuery({
    queryKey: ['reports'],
    queryFn: () => reportApi.list(),
  });

  // ---- hydration ----

  useEffect(() => {
    if (!dashboardQuery.data) return;
    metaForm.setFieldsValue({
      name: dashboardQuery.data.name,
      description: dashboardQuery.data.description,
      visibility: dashboardQuery.data.visibility,
    });
  }, [dashboardQuery.data, metaForm]);

  // ---- mutations ----

  const metaMut = useMutation({
    mutationFn: (values: DashboardMetaForm) =>
      dashboardApi.update(dashboardId, {
        name: values.name,
        // DashboardUpdate.description is `string | undefined` — Form passes
        // ``null`` for cleared fields, so coerce to ``undefined`` here.
        description: values.description ?? undefined,
        visibility: values.visibility,
      }),
    onSuccess: () => {
      message.success('看板信息已保存');
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId] });
      queryClient.invalidateQueries({ queryKey: ['dashboards'] });
    },
    onError: (err: Error) => message.error(`保存失败: ${err.message}`),
  });

  const layoutMut = useMutation({
    mutationFn: (entries: DashboardItemLayoutEntry[]) =>
      dashboardApi.updateLayout(dashboardId, entries),
    onSuccess: () => {
      // Layout change doesn't refetch items — the optimistic update on
      // the grid already reflects it. We still invalidate so other tabs
      // (DashboardView) pick up the new coords.
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId, 'items'] });
    },
    onError: (err: Error) => message.error(`布局更新失败: ${err.message}`),
  });

  const createItemMut = useMutation({
    mutationFn: (values: DashboardItemFormValues) =>
      dashboardApi.createItem(dashboardId, {
        ...values,
        // New items always land at the bottom of the grid; the user can
        // drag them after creation. Default size: 4x4 in the 12-col grid.
        x: 0,
        y: Number.MAX_SAFE_INTEGER,
        w: 4,
        h: 4,
      }),
    onSuccess: () => {
      message.success('看板项已创建');
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId, 'items'] });
      setCreatingItem(false);
    },
    onError: (err: Error) => message.error(`创建失败: ${err.message}`),
  });

  const updateItemMut = useMutation({
    mutationFn: ({ itemId, values }: { itemId: number; values: DashboardItemFormValues }) =>
      dashboardApi.updateItem(dashboardId, itemId, values),
    onSuccess: () => {
      message.success('看板项已保存');
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId, 'items'] });
      setEditingItem(null);
    },
    onError: (err: Error) => message.error(`保存失败: ${err.message}`),
  });

  const deleteItemMut = useMutation({
    mutationFn: (itemId: number) => dashboardApi.deleteItem(dashboardId, itemId),
    onSuccess: () => {
      message.success('看板项已删除');
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId, 'items'] });
    },
    onError: (err: Error) => message.error(`删除失败: ${err.message}`),
  });

  const createShareMut = useMutation({
    mutationFn: (payload: { user_id: number; permission: DashboardSharePermission }) =>
      dashboardApi.createShare(dashboardId, payload),
    onSuccess: () => {
      message.success('已分享');
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId, 'shares'] });
    },
    onError: (err: Error) => message.error(`分享失败: ${err.message}`),
  });

  const revokeShareMut = useMutation({
    mutationFn: (userId: number) => dashboardApi.revokeShare(dashboardId, userId),
    onSuccess: () => {
      message.success('已撤销');
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId, 'shares'] });
    },
    onError: (err: Error) => message.error(`撤销失败: ${err.message}`),
  });

  // ---- guards ----

  if (!Number.isFinite(dashboardId)) {
    return (
      <Result status="404" title="看板不存在" extra={<Link to="/dashboards">返回列表</Link>} />
    );
  }

  if (dashboardQuery.isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    );
  }

  if (dashboardQuery.error || !dashboardQuery.data) {
    return (
      <Result
        status="404"
        title="看板不存在或您无权编辑"
        extra={<Link to="/dashboards">返回列表</Link>}
      />
    );
  }

  const dashboard: Dashboard = dashboardQuery.data;
  const items: DashboardItem[] = itemsQuery.data ?? [];

  // ---- helpers ----

  // Derived from ``editingItem`` — no memo needed because the modal only
  // mounts when ``editingItem`` is non-null, so the object is computed
  // once per render anyway.
  const editingInitialValues: Partial<DashboardItemFormValues> = editingItem
    ? {
        item_type: editingItem.item_type,
        title: editingItem.title,
        report_id: editingItem.report_id,
        data_source_id: editingItem.data_source_id,
        table_name: editingItem.table_name,
        custom_sql: editingItem.custom_sql,
        fields: editingItem.fields ?? [],
        where_conditions: editingItem.where_conditions ?? [],
        group_by: editingItem.group_by ?? [],
        order_by: editingItem.order_by ?? [],
        limit: editingItem.limit,
        display_config: editingItem.display_config,
        parameters: editingItem.parameters ?? {},
        text_content: editingItem.text_content,
      }
    : {};

  // ---- render ----

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          编辑看板: {dashboard.name}
        </Title>
        <Space>
          <Button icon={<EyeOutlined />} onClick={() => navigate(`/dashboards/${dashboardId}`)}>
            预览
          </Button>
          <Button icon={<ShareAltOutlined />} onClick={() => setShareOpen(true)}>
            分享
          </Button>
        </Space>
      </Space>

      <Form
        form={metaForm}
        layout="inline"
        style={{ marginBottom: 16, rowGap: 8 }}
        onFinish={(values) => metaMut.mutate(values)}
      >
        <Form.Item name="name" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="看板名称" style={{ width: 240 }} />
        </Form.Item>
        <Form.Item name="visibility" rules={[{ required: true }]}>
          <Select
            style={{ width: 160 }}
            options={[
              { value: 'private', label: '私有' },
              { value: 'org', label: '部门' },
              { value: 'public', label: '公开' },
            ]}
          />
        </Form.Item>
        <Form.Item>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            htmlType="submit"
            loading={metaMut.isPending}
          >
            保存信息
          </Button>
        </Form.Item>
      </Form>

      {dashboardQuery.data.can_edit === false && (
        <Alert
          type="warning"
          showIcon
          message="您只有只读权限，编辑操作将被服务端拒绝。"
          style={{ marginBottom: 16 }}
        />
      )}

      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreatingItem(true)}>
          添加看板项
        </Button>
        <Text type="secondary">
          共 {items.length} 项。拖拽调整位置；批量布局会在拖动停止 250ms 后保存。
        </Text>
      </Space>

      <div style={{ position: 'relative', zIndex: 1 }}>
        <DashboardGridEditor
          items={items}
          onLayoutChange={(entries) => layoutMut.mutate(entries)}
          onItemClick={(item) => setEditingItem(item)}
          onItemDelete={(item) => deleteItemMut.mutate(item.id)}
          onOpenSource={handleOpenSource}
          deletingItemId={
            deleteItemMut.isPending && typeof deleteItemMut.variables === 'number'
              ? deleteItemMut.variables
              : null
          }
        />
      </div>

      <DashboardItemEditorModal
        visible={creatingItem}
        initialValues={{ item_type: 'report' }}
        dataSources={dsQuery.data}
        dataSourcesLoading={dsQuery.isLoading}
        reports={reportsQuery.data}
        reportsLoading={reportsQuery.isLoading}
        previewColumns={[]}
        onPreviewColumns={() => {
          /* Schema-aware suggestions not yet wired — operators type fields
             manually for now. Filled in when the data-source preview
             column listing gets promoted to a first-class API. */
        }}
        onSubmit={(values) => createItemMut.mutate(values)}
        onCancel={() => setCreatingItem(false)}
        submitPending={createItemMut.isPending}
      />

      <DashboardItemEditorModal
        visible={editingItem !== null}
        initialValues={editingInitialValues}
        dataSources={dsQuery.data}
        dataSourcesLoading={dsQuery.isLoading}
        reports={reportsQuery.data}
        reportsLoading={reportsQuery.isLoading}
        previewColumns={[]}
        onPreviewColumns={() => {
          /* Schema-aware suggestions not yet wired — operators type fields
             manually for now. Filled in when the data-source preview
             column listing gets promoted to a first-class API. */
        }}
        onSubmit={(values) => {
          if (!editingItem) return;
          updateItemMut.mutate({ itemId: editingItem.id, values });
        }}
        onCancel={() => setEditingItem(null)}
        submitPending={updateItemMut.isPending}
      />

      <DashboardShareModal
        visible={shareOpen}
        dashboard={dashboard}
        shares={sharesQuery.data as DashboardShare[] | undefined}
        sharesLoading={sharesQuery.isLoading}
        users={usersQuery.data}
        usersLoading={usersQuery.isLoading}
        onCreate={(payload) => createShareMut.mutate(payload)}
        onRevoke={(userId) => revokeShareMut.mutate(userId)}
        onCancel={() => setShareOpen(false)}
        createPending={createShareMut.isPending}
        revokePending={revokeShareMut.isPending}
      />
    </div>
  );
}
