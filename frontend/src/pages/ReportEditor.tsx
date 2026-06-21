import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card, Form, Input, Button, Space, Select, message, Modal,
  Tabs, InputNumber, Divider, Popconfirm
} from 'antd';
import {
  SaveOutlined, PlusOutlined, DeleteOutlined, DragOutlined,
  TableOutlined, BarChartOutlined, FontSizeOutlined,
  ArrowUpOutlined, ArrowDownOutlined, EyeOutlined, NumberOutlined
} from '@ant-design/icons';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Report, ReportItem, ReportItemCreate, ReportItemUpdate, DataSource } from '../types';
import { reportApi, dataSourceApi } from '../api';
import { formatError } from '../utils/error';

// ============ Sortable Item Component ============

interface SortableItemProps {
  id: string;
  item: ReportItem;
  index: number;
  onEdit: (item: ReportItem) => void;
  onDelete: (itemId: number) => void;
  onMoveUp: (index: number) => void;
  onMoveDown: (index: number) => void;
  isFirst: boolean;
  isLast: boolean;
}

function SortableItem({ id, item, index, onEdit, onDelete, onMoveUp, onMoveDown, isFirst, isLast }: SortableItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const getIcon = () => {
    switch (item.item_type) {
      case 'table': return <TableOutlined />;
      case 'chart': return <BarChartOutlined />;
      case 'metric': return <NumberOutlined />;
      case 'text': return <FontSizeOutlined />;
      default: return <TableOutlined />;
    }
  };

  return (
    <div ref={setNodeRef} style={{ ...style, marginBottom: 8 }}>
      <Card
        size="small"
        style={{
          borderLeft: `3px solid ${
            item.item_type === 'table' ? '#1890ff' :
            item.item_type === 'chart' ? '#faad14' :
            item.item_type === 'metric' ? '#52c41a' : '#722ed1'
          }`
        }}
        bodyStyle={{ padding: '8px 12px' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span {...attributes} {...listeners} style={{ cursor: 'grab', padding: '0 4px' }}>
            <DragOutlined />
          </span>

          <span style={{ fontSize: 16 }}>{getIcon()}</span>

          <span style={{ flex: 1, fontWeight: 500 }}>{item.name}</span>

          <span style={{ color: '#999', fontSize: 12 }}>
            {item.item_type === 'table' && `表: ${item.table_name || '-'}`}
            {item.item_type === 'chart' && `图表: ${item.display_config?.chart_type || '-'}`}
            {item.item_type === 'metric' && `指标`}
            {item.item_type === 'text' && `文本`}
          </span>

          <Space size="small">
            <Button
              type="text" size="small"
              icon={<ArrowUpOutlined />}
              disabled={isFirst}
              onClick={() => onMoveUp(index)}
            />
            <Button
              type="text" size="small"
              icon={<ArrowDownOutlined />}
              disabled={isLast}
              onClick={() => onMoveDown(index)}
            />
            <Button type="text" size="small" onClick={() => onEdit(item)}>
              编辑
            </Button>
            <Popconfirm title="确定删除?" onConfirm={() => onDelete(item.id)}>
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        </div>
      </Card>
    </div>
  );
}

// ============ Item Editor Modal ============

interface ItemEditorModalProps {
  visible: boolean;
  item: ReportItem | null;
  onSave: (item: ReportItemCreate | ReportItemUpdate) => void;
  onCancel: () => void;
  isNew: boolean;
}

function ItemEditorModal({ visible, item, onSave, onCancel, isNew }: ItemEditorModalProps) {
  const [form] = Form.useForm();
  // State initialized from item prop; onValuesChange keeps them in sync with form
  const [itemType, setItemType] = useState<string>(item?.item_type || 'table');
  const [useCustomSql, setUseCustomSql] = useState<boolean>(!!item?.custom_sql);

  // Keep itemType and useCustomSql in sync with form changes
  const handleValuesChange = (_: unknown, values: Record<string, unknown>) => {
    if (values.item_type && values.item_type !== itemType) {
      setItemType(values.item_type as string);
    }
    if (values.custom_sql !== undefined) {
      setUseCustomSql(!!values.custom_sql);
    }
  };

  // Sync form values when item changes
  useEffect(() => {
    if (item) {
      form.setFieldsValue({
        ...item,
        display_config: item.display_config || {},
      });
    } else {
      form.resetFields();
      form.setFieldsValue({
        item_type: 'table',
        order_index: 0,
        fields: [],
        where_conditions: [],
        group_by: [],
        order_by: [],
        limit: 1000,
        display_config: { height: 300 },
      });
    }
  }, [item, form]);

  const handleSubmit = () => {
    form.validateFields().then((values) => {
      const processedValues = {
        ...values,
        display_config: values.display_config || {},
      };
      // Remove display_config columns if empty
      if (processedValues.display_config && !Object.keys(processedValues.display_config).length) {
        delete processedValues.display_config;
      }
      onSave(processedValues);
    });
  };

  return (
    <Modal
      title={isNew ? '添加报表项' : '编辑报表项'}
      open={visible}
      onOk={handleSubmit}
      onCancel={onCancel}
      width={800}
      destroyOnClose
    >
      <Form form={form} layout="vertical" onValuesChange={handleValuesChange}>
        <Space style={{ width: '100%' }} size="large">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
            style={{ flex: 1 }}
          >
            <Input placeholder="例如: 月度销售额" />
          </Form.Item>

          <Form.Item
            name="item_type"
            label="类型"
            rules={[{ required: true, message: '请选择类型' }]}
            style={{ width: 150 }}
          >
            <Select onChange={(v) => setItemType(v)}>
              <Select.Option value="table">
                <Space><TableOutlined /> 表格</Space>
              </Select.Option>
              <Select.Option value="chart">
                <Space><BarChartOutlined /> 图表</Space>
              </Select.Option>
              <Select.Option value="metric">
                <Space><NumberOutlined /> 指标卡</Space>
              </Select.Option>
              <Select.Option value="text">
                <Space><FontSizeOutlined /> 文本</Space>
              </Select.Option>
            </Select>
          </Form.Item>

          <Form.Item name="order_index" label="排序" style={{ width: 100 }}>
            <InputNumber min={0} />
          </Form.Item>
        </Space>

        {itemType === 'text' ? (
          <Form.Item name={['display_config', 'content']} label="文本内容">
            <Input.TextArea rows={4} placeholder="输入静态文本内容..." />
          </Form.Item>
        ) : (
          <>
            <Divider>数据查询</Divider>

            <Form.Item label="使用自定义SQL">
              <Select value={useCustomSql ? 'yes' : 'no'} onChange={(v) => setUseCustomSql(v === 'yes')}>
                <Select.Option value="no">否，使用配置生成</Select.Option>
                <Select.Option value="yes">是，自定义SQL</Select.Option>
              </Select>
            </Form.Item>

            {useCustomSql ? (
              <Form.Item name="custom_sql" label="自定义SQL">
                <Input.TextArea rows={4} placeholder="SELECT * FROM table_name WHERE {param}..." />
              </Form.Item>
            ) : (
              <>
                <Form.Item
                  name="table_name"
                  label="表名"
                  rules={[{ required: true, message: '请输入表名' }]}
                >
                  <Input placeholder="schema.table_name" />
                </Form.Item>

                <Form.Item name="fields" label="查询字段">
                  <Select mode="tags" placeholder="field1, field2, SUM(amount) as total">
                    {form.getFieldValue('fields')?.map((f: string) => (
                      <Select.Option key={f} value={f}>{f}</Select.Option>
                    ))}
                  </Select>
                </Form.Item>

                <Card title="查询条件 (WHERE)" size="small">
                  <Form.List name="where_conditions">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <Space key={key} style={{ display: 'flex', marginBottom: 8 }} align="start">
                            <Form.Item name={[name, 'field']} style={{ margin: 0 }}>
                              <Input placeholder="字段" style={{ width: 120 }} />
                            </Form.Item>
                            <Form.Item name={[name, 'operator']} style={{ margin: 0 }}>
                              <Select style={{ width: 100 }}>
                                <Select.Option value="=">=</Select.Option>
                                <Select.Option value="!=">!=</Select.Option>
                                <Select.Option value=">">&gt;</Select.Option>
                                <Select.Option value=">=">&gt;=</Select.Option>
                                <Select.Option value="<">&lt;</Select.Option>
                                <Select.Option value="<=">&lt;=</Select.Option>
                                <Select.Option value="LIKE">LIKE</Select.Option>
                                <Select.Option value="IN">IN</Select.Option>
                              </Select>
                            </Form.Item>
                            <Form.Item name={[name, 'value']} style={{ margin: 0 }}>
                              <Input placeholder="值" style={{ width: 120 }} />
                            </Form.Item>
                            <Button type="text" danger onClick={() => remove(name)}>删除</Button>
                          </Space>
                        ))}
                        <Button type="dashed" onClick={add} block>+ 添加条件</Button>
                      </>
                    )}
                  </Form.List>
                </Card>

                <Form.Item name="group_by" label="GROUP BY 字段" style={{ marginTop: 16 }}>
                  <Select mode="tags" placeholder="category, region">
                    {form.getFieldValue('group_by')?.map((f: string) => (
                      <Select.Option key={f} value={f}>{f}</Select.Option>
                    ))}
                  </Select>
                </Form.Item>

                <Card title="排序 (ORDER BY)" size="small" style={{ marginTop: 16 }}>
                  <Form.List name="order_by">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <Space key={key} style={{ display: 'flex', marginBottom: 8 }} align="start">
                            <Form.Item name={[name, 'field']} style={{ margin: 0 }}>
                              <Input placeholder="字段" style={{ width: 120 }} />
                            </Form.Item>
                            <Form.Item name={[name, 'direction']} style={{ margin: 0 }}>
                              <Select style={{ width: 80 }}>
                                <Select.Option value="ASC">升序</Select.Option>
                                <Select.Option value="DESC">降序</Select.Option>
                              </Select>
                            </Form.Item>
                            <Button type="text" danger onClick={() => remove(name)}>删除</Button>
                          </Space>
                        ))}
                        <Button type="dashed" onClick={add} block>+ 添加排序</Button>
                      </>
                    )}
                  </Form.List>
                </Card>

                <Form.Item name="limit" label="返回行数限制" style={{ marginTop: 16 }}>
                  <InputNumber min={1} max={100000} defaultValue={1000} />
                </Form.Item>
              </>
            )}

            <Divider>展示配置</Divider>

            {itemType === 'chart' && (
              <>
                <Divider>图表配置</Divider>
                <Form.Item name={['display_config', 'chart_type']} label="图表类型">
                  <Select>
                    <Select.Option value="bar">柱状图</Select.Option>
                    <Select.Option value="horizontalBar">横向柱状图</Select.Option>
                    <Select.Option value="line">折线图</Select.Option>
                    <Select.Option value="area">面积图</Select.Option>
                    <Select.Option value="pie">饼图</Select.Option>
                    <Select.Option value="doughnut">环形图</Select.Option>
                    <Select.Option value="radar">雷达图</Select.Option>
                    <Select.Option value="polarArea">极坐标图</Select.Option>
                    <Select.Option value="scatter">散点图</Select.Option>
                    <Select.Option value="bubble">气泡图</Select.Option>
                  </Select>
                </Form.Item>

                <Form.Item name={['display_config', 'title']} label="图表标题">
                  <Input placeholder="输入图表标题" />
                </Form.Item>

                <Form.Item name={['display_config', 'subtitle']} label="副标题">
                  <Input placeholder="输入副标题（可选）" />
                </Form.Item>

                <Space style={{ width: '100%' }} size="large">
                  <Form.Item name={['display_config', 'height']} label="高度 (px)" style={{ flex: 1 }}>
                    <InputNumber min={200} max={800} defaultValue={400} />
                  </Form.Item>
                  <Form.Item name={['display_config', 'show_legend']} label="显示图例" valuePropName="checked">
                    <Select defaultValue={true}>
                      <Select.Option value={true}>是</Select.Option>
                      <Select.Option value={false}>否</Select.Option>
                    </Select>
                  </Form.Item>
                </Space>

                <Form.Item name={['display_config', 'legend_position']} label="图例位置">
                  <Select defaultValue="top">
                    <Select.Option value="top">顶部</Select.Option>
                    <Select.Option value="bottom">底部</Select.Option>
                    <Select.Option value="left">左侧</Select.Option>
                    <Select.Option value="right">右侧</Select.Option>
                  </Select>
                </Form.Item>

                <Space style={{ width: '100%' }} size="large">
                  <Form.Item name={['display_config', 'show_grid']} label="显示网格线" valuePropName="checked">
                    <Select defaultValue={true}>
                      <Select.Option value={true}>是</Select.Option>
                      <Select.Option value={false}>否</Select.Option>
                    </Select>
                  </Form.Item>
                  <Form.Item name={['display_config', 'stacked']} label="堆叠显示">
                    <Select defaultValue={false}>
                      <Select.Option value={true}>是</Select.Option>
                      <Select.Option value={false}>否</Select.Option>
                    </Select>
                  </Form.Item>
                </Space>
              </>
            )}

            {itemType === 'table' && (
              <>
                <Divider>表格配置</Divider>
                <Form.Item name={['display_config', 'title']} label="表格标题">
                  <Input placeholder="输入表格标题" />
                </Form.Item>
              </>
            )}
          </>
        )}
      </Form>
    </Modal>
  );
}

// ============ Main Editor ============

export default function ReportEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<Report | null>(null);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [itemModalVisible, setItemModalVisible] = useState(false);
  const [editingItem, setEditingItem] = useState<ReportItem | null>(null);
  const [activeTab, setActiveTab] = useState('config');

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const loadReport = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const data = await reportApi.get(Number(id));
      setReport(data);
    } catch (err: unknown) {
      message.error(formatError(err, '加载报表失败'));
    } finally {
      setLoading(false);
    }
  };

  const loadDataSources = async () => {
    try {
      const data = await dataSourceApi.list();
      setDataSources(data);
    } catch (err: unknown) {
      message.error(formatError(err, '加载数据源失败'));
    }
  };

  useEffect(() => {
    loadReport();
    loadDataSources();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadReport/loadDataSources use id from closure
  }, [id]);

  const handleSaveReport = async () => {
    if (!report) return;
    setSaving(true);
    try {
      await reportApi.update(report.id, {
        name: report.name,
        description: report.description,
        data_source_id: report.data_source_id,
        output_formats: report.output_formats,
        is_active: report.is_active,
      });
      message.success('保存成功');
    } catch (err: unknown) {
      message.error(formatError(err, '保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const handleAddItem = () => {
    setEditingItem(null);
    setItemModalVisible(true);
  };

  const handleEditItem = (item: ReportItem) => {
    setEditingItem(item);
    setItemModalVisible(true);
  };

  const handleSaveItem = async (itemData: ReportItemCreate | ReportItemUpdate) => {
    if (!report) return;
    try {
      if (editingItem) {
        await reportApi.updateItem(report.id, editingItem.id, itemData as ReportItemUpdate);
        message.success('更新成功');
      } else {
        await reportApi.createItem(report.id, itemData as ReportItemCreate);
        message.success('添加成功');
      }
      setItemModalVisible(false);
      loadReport();
    } catch (err: unknown) {
      message.error(formatError(err, '操作失败'));
    }
  };

  const handleDeleteItem = async (itemId: number) => {
    if (!report) return;
    try {
      await reportApi.deleteItem(report.id, itemId);
      message.success('删除成功');
      loadReport();
    } catch (err: unknown) {
      message.error(formatError(err, '删除失败'));
    }
  };

  const handleDragEnd = (event: DragEndEvent) => {
    if (!report) return;
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIndex = report.items.findIndex((i) => `item-${i.id}` === active.id);
      const newIndex = report.items.findIndex((i) => `item-${i.id}` === over.id);
      if (oldIndex !== -1 && newIndex !== -1) {
        const newItems = arrayMove(report.items, oldIndex, newIndex);
        const updatedItems = newItems.map((item, idx) => ({ ...item, order_index: idx }));
        setReport({ ...report, items: updatedItems as ReportItem[] });
        persistOrder(updatedItems as ReportItem[]);
      }
    }
  };

  const handleMoveItem = async (index: number, direction: 'up' | 'down') => {
    if (!report) return;
    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= report.items.length) return;

    const newItems = arrayMove(report.items, index, newIndex);
    const updatedItems = newItems.map((item, idx) => ({ ...item, order_index: idx }));
    setReport({ ...report, items: updatedItems as ReportItem[] });

    await persistOrder(updatedItems as ReportItem[]);
  };

  // Persist a new item ordering to the backend in a single atomic call.
  // On any failure, reload from DB so the UI reflects the actual server state
  // rather than the optimistic update.
  const persistOrder = async (orderedItems: ReportItem[]) => {
    if (!report) return;
    const payload = orderedItems
      .filter((i) => i.id !== undefined)
      .map((i) => ({ item_id: i.id as number, order_index: i.order_index }));
    if (payload.length === 0) return;
    try {
      await reportApi.reorderItems(report.id, payload);
    } catch (err: unknown) {
      message.error(formatError(err, '排序保存失败'));
      loadReport();
    }
  };

  if (loading) return <div style={{ padding: 24 }}>加载中...</div>;
  if (!report) return <div style={{ padding: 24 }}>报表不存在</div>;

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={() => navigate('/reports')}>返回</Button>
          <h2 style={{ margin: 0 }}>{report.name}</h2>
        </Space>
        <Space>
          <Button icon={<EyeOutlined />} onClick={() => navigate(`/reports/${report.id}/preview`)}>
            预览
          </Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSaveReport}>
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
              <Card title="基本配置">
                <Space direction="vertical" style={{ width: '100%' }} size="large">
                  <Space style={{ width: '100%' }}>
                    <Form.Item label="报表名称" style={{ flex: 1, margin: 0 }}>
                      <Input value={report.name} onChange={(e) => setReport({ ...report, name: e.target.value })} />
                    </Form.Item>
                    <Form.Item label="数据源" style={{ width: 200, margin: 0 }}>
                      <Select
                        value={report.data_source_id}
                        onChange={(v) => setReport({ ...report, data_source_id: v })}
                      >
                        {dataSources.map((ds) => (
                          <Select.Option key={ds.id} value={ds.id}>{ds.name}</Select.Option>
                        ))}
                      </Select>
                    </Form.Item>
                    <Form.Item label="状态" style={{ width: 100, margin: 0 }}>
                      <Select
                        value={report.is_active}
                        onChange={(v) => setReport({ ...report, is_active: v })}
                      >
                        <Select.Option value={true}>启用</Select.Option>
                        <Select.Option value={false}>禁用</Select.Option>
                      </Select>
                    </Form.Item>
                  </Space>
                  <Form.Item label="描述" style={{ margin: 0 }}>
                    <Input.TextArea
                      value={report.description || ''}
                      onChange={(e) => setReport({ ...report, description: e.target.value })}
                      rows={2}
                    />
                  </Form.Item>
                </Space>
              </Card>
            ),
          },
          {
            key: 'items',
            label: `报表项 (${report.items?.length || 0})`,
            children: (
              <Card
                title="报表项列表"
                extra={
                  <Button type="primary" icon={<PlusOutlined />} onClick={handleAddItem}>
                    添加报表项
                  </Button>
                }
              >
                <p style={{ color: '#999', marginBottom: 16 }}>
                  拖拽排序，点击编辑按钮配置报表项详情
                </p>

                {report.items && report.items.length > 0 ? (
                  <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                    <SortableContext
                      items={report.items.map((i) => `item-${i.id}`)}
                      strategy={verticalListSortingStrategy}
                    >
                      {report.items.map((item, index) => (
                        <SortableItem
                          key={`item-${item.id}`}
                          id={`item-${item.id}`}
                          item={item}
                          index={index}
                          onEdit={handleEditItem}
                          onDelete={handleDeleteItem}
                          onMoveUp={() => handleMoveItem(index, 'up')}
                          onMoveDown={() => handleMoveItem(index, 'down')}
                          isFirst={index === 0}
                          isLast={index === report.items.length - 1}
                        />
                      ))}
                    </SortableContext>
                  </DndContext>
                ) : (
                  <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                    暂无报表项，点击上方按钮添加
                  </div>
                )}
              </Card>
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
      />
    </div>
  );
}
