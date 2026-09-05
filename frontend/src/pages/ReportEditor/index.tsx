import { useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Tabs, Button, Space, message, Modal, Form, Input, Radio } from 'antd';
import { SaveOutlined, EyeOutlined, AppstoreOutlined, HistoryOutlined } from '@ant-design/icons';
import { arrayMove } from '@dnd-kit/sortable';
import type { DragEndEvent } from '@dnd-kit/core';
import type {
  Report,
  ReportItem,
  ReportItemCreate,
  ReportItemUpdate,
  ReportParameter,
  ReportParameterCreate,
  ReportParameterUpdate,
  ReportVisibility,
} from '../../types';
import { formatError } from '../../utils/error';
import {
  useCreateReportItem,
  useDeleteReportItem,
  useReport,
  useReorderReportItems,
  useUpdateReport,
  useUpdateReportItem,
} from '../../queries/useReports';
import {
  useCreateReportParameter,
  useDeleteReportParameter,
  useReportParameters,
  useUpdateReportParameter,
} from '../../queries/useParameters';
import { useDataSources } from '../../queries/useDataSources';
import { useSaveAsTemplate } from '../../queries/useReportTemplates';
import { useReportVersions } from '../../queries/useReportVersions';
import { useMe } from '../../queries/useAuth';
import { CardSkeleton } from '../../components/Skeleton';
import { SaveVersionModal } from '../../components/SaveVersionModal';
import { ConfigTab } from './ConfigTab';
import { ItemsTab } from './ItemsTab';
import { ParametersTab } from './ParametersTab';
import { ItemEditorModal } from './ItemEditorModal';
import { ParameterEditorModal } from './ParameterEditorModal';
import { sortedItemsByOrder } from './itemsView';

export default function ReportEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const reportId = id ? Number(id) : null;
  // B (post-批-report-versioning): drive the "查看历史" badge so
  // editors know at a glance how many versions they've snapshotted.
  // Cheap — React Query caches against ``report-versions/<id>`` which
  // the history page itself populates.
  const { data: versions = [] } = useReportVersions(reportId);

  // Server truth from React Query cache. The cache is the source for
  // items list and is what the page re-reads after any mutation.
  const { data: report, isPending: reportLoading } = useReport(reportId);
  // Edit buffer: a local copy of the report for unsaved edits in the
  // "报表配置" tab. Initialized from the cache once it arrives.
  const [buffer, setBuffer] = useState<Report | null>(null);
  const [bufferHydrated, setBufferHydrated] = useState(false);
  useEffect(() => {
    if (!bufferHydrated && report) {
      setBuffer(report);
      setBufferHydrated(true);
    }
  }, [report, bufferHydrated]);

  const { data: dataSources = [], isPending: dsLoading } = useDataSources();
  const updateReport = useUpdateReport();
  const createItem = useCreateReportItem(reportId ?? -1);
  const updateItem = useUpdateReportItem(reportId ?? -1);
  const deleteItem = useDeleteReportItem(reportId ?? -1);
  const reorderItems = useReorderReportItems(reportId ?? -1);

  // ---- Parameters (批 4b) -----------------------------------------------
  const parametersQ = useReportParameters(reportId);
  const createParam = useCreateReportParameter(reportId ?? -1);
  const updateParam = useUpdateReportParameter(reportId ?? -1);
  const deleteParam = useDeleteReportParameter(reportId ?? -1);
  const parameters = parametersQ.data ?? [];

  const [paramModalVisible, setParamModalVisible] = useState(false);
  const [editingParam, setEditingParam] = useState<ReportParameter | null>(null);

  const handleAddParam = () => {
    setEditingParam(null);
    setParamModalVisible(true);
  };

  const handleEditParam = (p: ReportParameter) => {
    setEditingParam(p);
    setParamModalVisible(true);
  };

  const handleDeleteParam = (paramId: number) => {
    deleteParam.mutate(paramId, {
      onSuccess: () => message.success('参数已删除'),
      onError: (err) => message.error(formatError(err, '删除失败')),
    });
  };

  const handleSaveParam = (payload: ReportParameterCreate | ReportParameterUpdate) => {
    if (!reportId) return;
    if (editingParam) {
      updateParam.mutate(
        { paramId: editingParam.id, payload: payload as ReportParameterUpdate },
        {
          onSuccess: () => {
            message.success('参数已更新');
            setParamModalVisible(false);
          },
          onError: (err) => message.error(formatError(err, '保存失败')),
        },
      );
    } else {
      createParam.mutate(payload as ReportParameterCreate, {
        onSuccess: () => {
          message.success('参数已创建');
          setParamModalVisible(false);
        },
        onError: (err) => message.error(formatError(err, '创建失败')),
      });
    }
  };

  const [itemModalVisible, setItemModalVisible] = useState(false);
  const [editingItem, setEditingItem] = useState<ReportItem | null>(null);
  const [activeTab, setActiveTab] = useState('config');
  const [saveVersionOpen, setSaveVersionOpen] = useState(false);

  // ---- 批 13: save-as-template (owner-or-admin) ----------------------
  // Pulls role + user id from useMe so we can hide the button for
  // non-owner non-admin users. The backend enforces the same — we
  // just hide the affordance so people don't see a 403 on click.
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  const isOwner = me.data?.user_id != null && buffer?.owner_user_id === me.data.user_id;
  const canSaveAsTemplate = isAdmin || isOwner;

  const [saveAsTemplateOpen, setSaveAsTemplateOpen] = useState(false);
  const [templateForm] = Form.useForm<{ visibility: ReportVisibility; category?: string }>();
  const saveAsTemplate = useSaveAsTemplate();

  const handleSaveAsTemplate = async () => {
    if (!reportId) return;
    try {
      const values = await templateForm.validateFields();
      saveAsTemplate.mutate(
        { reportId, payload: { visibility: values.visibility, category: values.category } },
        {
          onSuccess: (tpl) => {
            message.success(`已发布为模板「${tpl.name}」`);
            setSaveAsTemplateOpen(false);
            templateForm.resetFields();
          },
          onError: (err) => message.error(formatError(err, '发布失败')),
        },
      );
    } catch {
      // Form validation error — antd already shows inline messages.
    }
  };

  const handleSaveReport = () => {
    if (!buffer || !reportId) return;
    updateReport.mutate(
      {
        id: reportId,
        payload: {
          name: buffer.name,
          description: buffer.description,
          data_source_id: buffer.data_source_id,
          output_formats: buffer.output_formats,
          is_active: buffer.is_active,
        },
      },
      {
        onSuccess: () => message.success('保存成功'),
        // Rollback handled by useUpdateReport's onError (writes prev back
        // into the cache); the buffer follows the cache via the next
        // refetch from onSettled's invalidation, so no manual setBuffer
        // is needed on error.
        onError: (err) => message.error(formatError(err, '保存失败')),
      },
    );
  };

  const handleAddItem = () => {
    setEditingItem(null);
    setItemModalVisible(true);
  };

  const handleEditItem = (item: ReportItem) => {
    setEditingItem(item);
    setItemModalVisible(true);
  };

  const handleSaveItem = (itemData: ReportItemCreate | ReportItemUpdate) => {
    if (!reportId) return;
    const onDone = () => setItemModalVisible(false);
    if (editingItem) {
      updateItem.mutate(
        { itemId: editingItem.id, payload: itemData as ReportItemUpdate },
        {
          onSuccess: () => {
            message.success('更新成功');
            onDone();
          },
          onError: (err) => message.error(formatError(err, '操作失败')),
        },
      );
    } else {
      createItem.mutate(itemData as ReportItemCreate, {
        onSuccess: () => {
          message.success('添加成功');
          onDone();
        },
        onError: (err) => message.error(formatError(err, '操作失败')),
      });
    }
  };

  const handleDeleteItem = (itemId: number) => {
    deleteItem.mutate(itemId, {
      onSuccess: () => message.success('删除成功'),
      onError: (err) => message.error(formatError(err, '删除失败')),
    });
  };

  // Items list shown to the user. Items derive from the React Query
  // cache (server truth) — every item mutation already patches the
  // cache optimistically (see ``useCreateReportItem`` / ``useDeleteReportItem``
  // / ``useUpdateReportItem`` / ``useReorderReportItems``), so the list
  // stays in sync without any local-state mirror. The previous design
  // read items from a once-hydrated ``buffer`` copy, which left the tab
  // stale after every create/delete/update and forced the drag-reorder
  // path to manually ``setBuffer`` as a workaround (TODO-4, fixed here).
  const itemsView = useMemo(
    () => sortedItemsByOrder(report?.items),
    [report],
  );

  const persistOrder = (orderedItems: ReportItem[]) => {
    if (!reportId) return;
    const payload = orderedItems
      .filter((i) => i.id !== undefined)
      .map((i) => ({ item_id: i.id as number, order_index: i.order_index }));
    if (payload.length === 0) return;
    reorderItems.mutate(payload, {
      onError: (err) => message.error(formatError(err, '排序保存失败')),
    });
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIndex = itemsView.findIndex((i) => `item-${i.id}` === active.id);
      const newIndex = itemsView.findIndex((i) => `item-${i.id}` === over.id);
      if (oldIndex !== -1 && newIndex !== -1) {
        const newItems = arrayMove(itemsView, oldIndex, newIndex);
        const updatedItems = newItems.map((item, idx) => ({ ...item, order_index: idx }));
        persistOrder(updatedItems);
      }
    }
  };

  const handleMoveItem = (index: number, direction: 'up' | 'down') => {
    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= itemsView.length) return;
    const newItems = arrayMove(itemsView, index, newIndex);
    const updatedItems = newItems.map((item, idx) => ({ ...item, order_index: idx }));
    persistOrder(updatedItems);
  };

  if (reportLoading || dsLoading)
    return (
      <div style={{ padding: 24 }}>
        <CardSkeleton rows={6} />
      </div>
    );
  if (!report || !buffer) return <div style={{ padding: 24 }}>报表不存在</div>;

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={() => navigate('/reports')}>返回</Button>
          <h2 style={{ margin: 0 }}>{buffer.name}</h2>
        </Space>
        <Space>
          <Button icon={<EyeOutlined />} onClick={() => navigate(`/reports/${buffer.id}/preview`)}>
            预览
          </Button>
          <Button
            icon={<HistoryOutlined />}
            onClick={() => navigate(`/reports/${buffer.id}/history`)}
            disabled={!reportId}
          >
            查看历史{versions.length > 0 ? ` (${versions.length})` : ''}
          </Button>
          <Button
            icon={<SaveOutlined />}
            onClick={() => setSaveVersionOpen(true)}
            disabled={!reportId}
          >
            保存为版本
          </Button>
          {/* 批 13 — owner-or-admin only. Backend enforces the same
              gate, but we hide the button so non-owners don't see a
              403. The Modal collects visibility + category; the
              server strips scheduler + notification_config from
              the cloned template row. */}
          {canSaveAsTemplate && (
            <Button
              icon={<AppstoreOutlined />}
              onClick={() => {
                templateForm.setFieldsValue({ visibility: 'public', category: '' });
                setSaveAsTemplateOpen(true);
              }}
              disabled={!reportId}
            >
              另存为模板
            </Button>
          )}
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={updateReport.isPending}
            onClick={handleSaveReport}
          >
            保存
          </Button>
        </Space>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'config',
            label: '报表配置',
            children: (
              <ConfigTab buffer={buffer} dataSources={dataSources} onBufferChange={setBuffer} />
            ),
          },
          {
            key: 'items',
            label: `报表项 (${report?.items?.length ?? 0})`,
            children: (
              <ItemsTab
                items={itemsView}
                onAdd={handleAddItem}
                onEdit={handleEditItem}
                onDelete={handleDeleteItem}
                onMoveUp={(index) => handleMoveItem(index, 'up')}
                onMoveDown={(index) => handleMoveItem(index, 'down')}
                onDragEnd={handleDragEnd}
              />
            ),
          },
          {
            key: 'parameters',
            label: `参数 (${parameters.length})`,
            children: (
              <ParametersTab
                parameters={parameters}
                onAdd={handleAddParam}
                onEdit={handleEditParam}
                onDelete={handleDeleteParam}
              />
            ),
          },
        ]}
      />

      <ItemEditorModal
        visible={itemModalVisible}
        item={editingItem}
        onSave={handleSaveItem}
        onCancel={() => setItemModalVisible(false)}
        isNew={!editingItem}
        saving={createItem.isPending || updateItem.isPending}
      />

      <ParameterEditorModal
        visible={paramModalVisible}
        parameter={editingParam}
        onSave={handleSaveParam}
        onCancel={() => setParamModalVisible(false)}
        saving={createParam.isPending || updateParam.isPending}
      />

      {reportId && (
        <SaveVersionModal
          open={saveVersionOpen}
          reportId={reportId}
          onClose={() => setSaveVersionOpen(false)}
        />
      )}

      {/* 批 13 — save-as-template modal. Visibility radio +
          free-text category; the backend validates
          ``visibility`` against the ReportVisibility Literal
          and caps ``category`` at 64 chars. */}
      <Modal
        title="另存为模板"
        open={saveAsTemplateOpen}
        onOk={handleSaveAsTemplate}
        onCancel={() => setSaveAsTemplateOpen(false)}
        confirmLoading={saveAsTemplate.isPending}
        okText="发布"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={templateForm} layout="vertical" preserve={false}>
          <Form.Item
            name="visibility"
            label="可见性"
            rules={[{ required: true, message: '请选择可见性' }]}
            initialValue="public"
          >
            <Radio.Group>
              <Radio.Button value="public">公开（所有人可 fork）</Radio.Button>
              <Radio.Button value="org">同部门</Radio.Button>
              <Radio.Button value="private">私有（仅我自己）</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item
            name="category"
            label="分类"
            tooltip="可选；用于模板市场筛选。最长 64 字符。"
          >
            <Input placeholder="例如: 财务分析、销售看板" maxLength={64} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
