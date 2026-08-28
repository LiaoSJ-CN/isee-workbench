/** Editor for a single Dashboard item (批 14.3).
 *
 * Three flavours mirroring :class:`DashboardItemType`:
 *
 * - ``report`` — pick an existing report. The backend treats the report as a
 *   black box; we forward the id only. Title falls back to the report name.
 * - ``chart`` — inline query builder (table_name + fields + where / group /
 *   order / limit) plus display_config. The Chart.js renderer lives on the
 *   backend's :func:`render_chart_item` (preview is server-side, so the
 *   frontend doesn't need a Chart.js dep).
 * - ``text`` — raw escaped text content, surfaced as ``white-space: pre-wrap``.
 *
 * The form's fields switch on ``item_type``; we reset the irrelevant ones when
 * the type changes so a half-typed chart query doesn't leak into a new text
 * item on save.
 */

import { useEffect, useState } from 'react';
import {
  Alert,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Space,
  Tabs,
} from 'antd';

import type {
  DataSource,
  DisplayConfig,
  OrderByItem,
  Report,
  WhereCondition,
} from '../types';

export type DashboardItemType = 'report' | 'chart' | 'text';

export interface DashboardItemFormValues {
  item_type: DashboardItemType;
  title?: string | null;
  // Report
  report_id?: number | null;
  // Chart / custom-SQL
  data_source_id?: number | null;
  table_name?: string | null;
  custom_sql?: string | null;
  fields: string[];
  where_conditions: WhereCondition[];
  group_by: string[];
  order_by: OrderByItem[];
  limit?: number | null;
  display_config?: DisplayConfig | null;
  parameters: Record<string, unknown>;
  // Text
  text_content?: string | null;
}

export interface DashboardItemEditorModalProps {
  visible: boolean;
  initialValues: Partial<DashboardItemFormValues>;
  dataSources: DataSource[] | undefined;
  dataSourcesLoading: boolean;
  reports: Report[] | undefined;
  reportsLoading: boolean;
  /** Preview columns for the active data source, used to populate field suggestions. */
  previewColumns: string[];
  onPreviewColumns: (dataSourceId: number) => void;
  onSubmit: (values: DashboardItemFormValues) => void;
  onCancel: () => void;
  submitPending: boolean;
}

export function DashboardItemEditorModal({
  visible,
  initialValues,
  dataSources,
  dataSourcesLoading,
  reports,
  reportsLoading,
  previewColumns,
  onPreviewColumns,
  onSubmit,
  onCancel,
  submitPending,
}: DashboardItemEditorModalProps) {
  const [form] = Form.useForm<DashboardItemFormValues>();
  const [itemType, setItemType] = useState<DashboardItemType>(
    initialValues.item_type ?? 'report',
  );

  // Reset the form whenever the modal is reopened with new initial values.
  // The dashboard page passes either a fresh item (id undefined) or an
  // existing one (id present) — both arrive via initialValues.
  useEffect(() => {
    if (!visible) return;
    const merged: DashboardItemFormValues = {
      item_type: initialValues.item_type ?? 'report',
      title: initialValues.title ?? null,
      report_id: initialValues.report_id ?? null,
      data_source_id: initialValues.data_source_id ?? null,
      table_name: initialValues.table_name ?? null,
      custom_sql: initialValues.custom_sql ?? null,
      fields: initialValues.fields ?? [],
      where_conditions: initialValues.where_conditions ?? [],
      group_by: initialValues.group_by ?? [],
      order_by: initialValues.order_by ?? [],
      limit: initialValues.limit ?? null,
      display_config: initialValues.display_config ?? null,
      parameters: initialValues.parameters ?? {},
      text_content: initialValues.text_content ?? null,
    };
    form.setFieldsValue(merged);
    setItemType(merged.item_type);
  }, [visible, initialValues, form]);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      onSubmit(values);
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    }
  };

  // Field suggestion source — previewColumns from the parent, fall back to a
  // small static list so the AutoComplete is still useful before preview runs.
  const fieldOptions = previewColumns.length
    ? previewColumns.map((c) => ({ value: c }))
    : [{ value: '*' }];

  const dsOptions = (dataSources ?? []).map((d) => ({
    value: d.id,
    label: d.name,
  }));

  const reportOptions = (reports ?? []).map((r) => ({
    value: r.id,
    label: r.name,
  }));

  return (
    <Modal
      title={initialValues.report_id ? '编辑看板项' : '新建看板项'}
      open={visible}
      onCancel={onCancel}
      onOk={handleSubmit}
      confirmLoading={submitPending}
      okText="保存"
      cancelText="取消"
      width={720}
      destroyOnClose
    >
      <Form<DashboardItemFormValues> form={form} layout="vertical">
        <Form.Item label="类型" name="item_type" rules={[{ required: true }]}>
          <Select
            options={[
              { value: 'report', label: '报表引用' },
              { value: 'chart', label: '图表（SQL 查询）' },
              { value: 'text', label: '文本' },
            ]}
            onChange={(v: DashboardItemType) => setItemType(v)}
          />
        </Form.Item>
        <Form.Item label="标题（可选）" name="title">
          <Input placeholder="给这个看板项起个名字" allowClear />
        </Form.Item>

        <Tabs
          activeKey={itemType}
          onChange={(k) => setItemType(k as DashboardItemType)}
          items={[
            {
              key: 'report',
              label: '报表',
              forceRender: true,
              children: (
                <Form.Item
                  label="引用报表"
                  name="report_id"
                  rules={[{ required: itemType === 'report', message: '请选择报表' }]}
                >
                  <Select
                    placeholder="选择已有报表"
                    options={reportOptions}
                    loading={reportsLoading}
                    showSearch
                    optionFilterProp="label"
                    allowClear
                  />
                </Form.Item>
              ),
            },
            {
              key: 'chart',
              label: '图表',
              forceRender: true,
              children: (
                <Space direction="vertical" style={{ width: '100%' }} size="middle">
                  <Form.Item
                    label="数据源"
                    name="data_source_id"
                    rules={[{ required: itemType === 'chart', message: '请选择数据源' }]}
                  >
                    <Select
                      placeholder="选择数据源"
                      options={dsOptions}
                      loading={dataSourcesLoading}
                      showSearch
                      optionFilterProp="label"
                      onChange={(v: number) => {
                        if (v) onPreviewColumns(v);
                      }}
                      allowClear
                    />
                  </Form.Item>
                  <Form.Item label="表名" name="table_name">
                    <Input placeholder="例：orders（custom_sql 与 table_name 二选一）" allowClear />
                  </Form.Item>
                  <Form.Item label="自定义 SQL（可覆盖 table_name）" name="custom_sql">
                    <Input.TextArea
                      rows={3}
                      placeholder="SELECT {fields} FROM {table} WHERE ..."
                      allowClear
                    />
                  </Form.Item>
                  <Form.Item label="查询字段" name="fields">
                    <Select
                      mode="tags"
                      placeholder="选择或输入字段名"
                      options={fieldOptions}
                      tokenSeparators={[',', ' ']}
                    />
                  </Form.Item>
                  <Form.Item label="分组字段" name="group_by">
                    <Select
                      mode="tags"
                      placeholder="GROUP BY 字段"
                      options={fieldOptions}
                      tokenSeparators={[',', ' ']}
                    />
                  </Form.Item>
                  <Form.Item label="排序" name="order_by">
                    <Select
                      mode="tags"
                      placeholder="order_by: <field>:<asc|desc>"
                      tokenSeparators={[',']}
                    />
                  </Form.Item>
                  <Form.Item label="LIMIT" name="limit">
                    <InputNumber min={0} max={100000} style={{ width: 160 }} placeholder="例：100" />
                  </Form.Item>
                  <Alert
                    type="info"
                    showIcon
                    message="图表渲染依赖后端 Chart.js；预览请使用编辑器内「预览」按钮。"
                  />
                </Space>
              ),
            },
            {
              key: 'text',
              label: '文本',
              forceRender: true,
              children: (
                <Form.Item
                  label="文本内容"
                  name="text_content"
                  rules={[{ required: itemType === 'text', message: '请输入文本' }]}
                >
                  <Input.TextArea
                    rows={8}
                    placeholder="支持换行；后端会 html.escape 处理 XSS"
                  />
                </Form.Item>
              ),
            },
          ]}
        />
      </Form>
    </Modal>
  );
}
