import { useEffect, useState } from 'react';
import {
  Modal,
  Form,
  Input,
  Select,
  InputNumber,
  Divider,
  Card,
  Button,
  Space,
  message,
} from 'antd';
import {
  TableOutlined,
  BarChartOutlined,
  NumberOutlined,
  FontSizeOutlined,
} from '@ant-design/icons';
import type { ReportItem, ReportItemCreate, ReportItemUpdate } from '../../types';

export interface ItemEditorModalProps {
  visible: boolean;
  item: ReportItem | null;
  onSave: (item: ReportItemCreate | ReportItemUpdate) => void;
  onCancel: () => void;
  isNew: boolean;
  saving?: boolean;
}

export function ItemEditorModal({
  visible,
  item,
  onSave,
  onCancel,
  isNew,
  saving,
}: ItemEditorModalProps) {
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
    form
      .validateFields()
      .then((values) => {
        // Item-type-specific validation beyond what Form.Item rules cover
        if (itemType === 'chart' && !values.display_config?.chart_type) {
          message.warning('图表类型必须选择图表类型（如柱状图、折线图等）');
          return;
        }
        if (itemType === 'text' && !values.display_config?.content?.trim()) {
          message.warning('文本类型必须填写文本内容');
          return;
        }
        const needsFields = ['table', 'chart', 'metric'].includes(itemType) && !useCustomSql;
        if (needsFields && (!values.fields || values.fields.length === 0)) {
          message.warning('至少需要一个查询字段');
          return;
        }
        const processedValues = {
          ...values,
          display_config: values.display_config || {},
        };
        // Remove display_config columns if empty
        if (processedValues.display_config && !Object.keys(processedValues.display_config).length) {
          delete processedValues.display_config;
        }
        onSave(processedValues);
      })
      .catch(() => {
        // Ant Design already highlights invalid fields — no extra handling needed.
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
      confirmLoading={saving}
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
                <Space>
                  <TableOutlined /> 表格
                </Space>
              </Select.Option>
              <Select.Option value="chart">
                <Space>
                  <BarChartOutlined /> 图表
                </Space>
              </Select.Option>
              <Select.Option value="metric">
                <Space>
                  <NumberOutlined /> 指标卡
                </Space>
              </Select.Option>
              <Select.Option value="text">
                <Space>
                  <FontSizeOutlined /> 文本
                </Space>
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
              <Select
                value={useCustomSql ? 'yes' : 'no'}
                onChange={(v) => setUseCustomSql(v === 'yes')}
              >
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
                      <Select.Option key={f} value={f}>
                        {f}
                      </Select.Option>
                    ))}
                  </Select>
                </Form.Item>

                <Card title="查询条件 (WHERE)" size="small">
                  <Form.List name="where_conditions">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <Space
                            key={key}
                            style={{ display: 'flex', marginBottom: 8 }}
                            align="start"
                          >
                            <Form.Item name={[name, 'field']} style={{ margin: 0 }}>
                              <Input placeholder="字段" style={{ width: 120 }} />
                            </Form.Item>
                            <Form.Item name={[name, 'operator']} style={{ margin: 0 }}>
                              <Select style={{ width: 120 }}>
                                <Select.Option value="=">=</Select.Option>
                                <Select.Option value="!=">!=</Select.Option>
                                <Select.Option value=">">&gt;</Select.Option>
                                <Select.Option value=">=">&gt;=</Select.Option>
                                <Select.Option value="<">&lt;</Select.Option>
                                <Select.Option value="<=">&lt;=</Select.Option>
                                <Select.Option value="LIKE">LIKE</Select.Option>
                                <Select.Option value="IN">IN</Select.Option>
                                <Select.Option value="IS NULL">IS NULL</Select.Option>
                                <Select.Option value="IS NOT NULL">IS NOT NULL</Select.Option>
                              </Select>
                            </Form.Item>
                            <Form.Item name={[name, 'value']} style={{ margin: 0 }}>
                              <Input placeholder="值" style={{ width: 120 }} />
                            </Form.Item>
                            <Button type="text" danger onClick={() => remove(name)}>
                              删除
                            </Button>
                          </Space>
                        ))}
                        <Button type="dashed" onClick={add} block>
                          + 添加条件
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Card>

                <Form.Item name="group_by" label="GROUP BY 字段" style={{ marginTop: 16 }}>
                  <Select mode="tags" placeholder="category, region">
                    {form.getFieldValue('group_by')?.map((f: string) => (
                      <Select.Option key={f} value={f}>
                        {f}
                      </Select.Option>
                    ))}
                  </Select>
                </Form.Item>

                <Card title="排序 (ORDER BY)" size="small" style={{ marginTop: 16 }}>
                  <Form.List name="order_by">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <Space
                            key={key}
                            style={{ display: 'flex', marginBottom: 8 }}
                            align="start"
                          >
                            <Form.Item name={[name, 'field']} style={{ margin: 0 }}>
                              <Input placeholder="字段" style={{ width: 120 }} />
                            </Form.Item>
                            <Form.Item name={[name, 'direction']} style={{ margin: 0 }}>
                              <Select style={{ width: 80 }}>
                                <Select.Option value="ASC">升序</Select.Option>
                                <Select.Option value="DESC">降序</Select.Option>
                              </Select>
                            </Form.Item>
                            <Button type="text" danger onClick={() => remove(name)}>
                              删除
                            </Button>
                          </Space>
                        ))}
                        <Button type="dashed" onClick={add} block>
                          + 添加排序
                        </Button>
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
                  <Form.Item
                    name={['display_config', 'height']}
                    label="高度 (px)"
                    style={{ flex: 1 }}
                  >
                    <InputNumber min={200} max={800} defaultValue={400} />
                  </Form.Item>
                  <Form.Item
                    name={['display_config', 'show_legend']}
                    label="显示图例"
                    valuePropName="checked"
                  >
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
                  <Form.Item
                    name={['display_config', 'show_grid']}
                    label="显示网格线"
                    valuePropName="checked"
                  >
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
