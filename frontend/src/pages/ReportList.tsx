import { useState, useEffect } from 'react';
import { Table, Button, Space, Modal, Form, Input, Select, message, Popconfirm, Tag, Alert } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, PlayCircleOutlined, EyeOutlined, ClockCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import type { Report, ReportCreate, DataSource } from '../types';
import { reportApi, dataSourceApi } from '../api';
import { formatError } from '../utils/error';

export default function ReportList() {
  const [reports, setReports] = useState<Report[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [form] = Form.useForm<ReportCreate>();
  const navigate = useNavigate();
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10, total: 0 });

  const loadReports = async () => {
    setLoading(true);
    try {
      const data = await reportApi.list();
      setReports(data);
      setPagination((prev) => ({ ...prev, total: data.length }));
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
    loadReports();
    loadDataSources();
     
  }, []);

  const handleCreate = () => {
    form.resetFields();
    form.setFieldsValue({ output_formats: ['excel', 'html'], is_active: true });
    setModalVisible(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      await reportApi.create(values);
      message.success('创建成功');
      setModalVisible(false);
      loadReports();
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    }
  };

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择要删除的报表');
      return;
    }

    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>确定要删除选中的 {selectedRowKeys.length} 个报表吗？</p>
          <Alert type="warning" message="报表删除后无法恢复，请谨慎操作！" />
        </div>
      ),
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await Promise.all(
            selectedRowKeys.map(id => reportApi.delete(id as number))
          );
          message.success(`成功删除 ${selectedRowKeys.length} 个报表`);
          setSelectedRowKeys([]);
          loadReports();
        } catch (err: unknown) {
          message.error(formatError(err, '删除失败'));
        }
      },
    });
  };

  const handleDelete = async (id: number) => {
    try {
      await reportApi.delete(id);
      message.success('删除成功');
      loadReports();
    } catch (err: unknown) {
      message.error(formatError(err, '删除失败'));
    }
  };

  const handleGenerate = async (report: Report, format: 'excel' | 'html') => {
    try {
      message.loading({ content: '正在生成报表...', key: 'export' });
      await reportApi.generate(report.id, format);
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 15);
      const filename = `${report.name}_${timestamp}.${format}`;
      await reportApi.download(report.id, format, filename);
      message.success({ content: `${format.toUpperCase()} 下载成功`, key: 'export' });
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error({ content: error.response?.data?.detail || '生成失败', key: 'export' });
    }
  };

  const handleTableChange = (pag: { current?: number; pageSize?: number }) => {
    setPagination(prev => ({
      ...prev,
      current: pag.current || 1,
      pageSize: pag.pageSize || 10,
    }));
  };

  const rowSelection = {
    selectedRowKeys,
    onChange: (keys: React.Key[]) => {
      setSelectedRowKeys(keys);
    },
  };

  const columns: ColumnsType<Report> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name, record) => (
        <a onClick={() => navigate(`/reports/${record.id}`)}>{name}</a>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '数据源',
      dataIndex: 'data_source_id',
      key: 'data_source',
      width: 150,
      render: (dsId) => {
        const ds = dataSources.find((d) => d.id === dsId);
        return ds ? ds.name : `ID: ${dsId}`;
      },
    },
    {
      title: '报表项',
      dataIndex: 'items',
      key: 'items',
      width: 80,
      render: (items) => items?.length || 0,
    },
    {
      title: '定时任务',
      key: 'schedule',
      width: 120,
      render: (_, record) => (
        record.is_scheduled ? (
          <Tag icon={<ClockCircleOutlined />} color="green">
            {record.cron_expression || '已配置'}
          </Tag>
        ) : (
          <Tag>未配置</Tag>
        )
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active) => active ? <Tag color="green">启用</Tag> : <Tag color="gray">禁用</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_, record) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => navigate(`/reports/${record.id}`)}>
            编辑
          </Button>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => navigate(`/reports/${record.id}/preview`)}>
            预览
          </Button>
          <Button type="link" size="small" icon={<PlayCircleOutlined />} onClick={() => handleGenerate(record, 'excel')}>
            Excel
          </Button>
          <Popconfirm title="确定删除?" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>报表管理</h2>
        <Space>
          <Button
            danger
            icon={<DeleteOutlined />}
            disabled={selectedRowKeys.length === 0}
            onClick={handleBatchDelete}
          >
            批量删除 {selectedRowKeys.length > 0 ? `(${selectedRowKeys.length})` : ''}
          </Button>
          <Button icon={<ClockCircleOutlined />} onClick={() => navigate('/scheduler')}>
            定时任务
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            创建报表
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={reports}
        rowKey="id"
        loading={loading}
        rowSelection={rowSelection}
        scroll={{ x: 'max-content' }}
        pagination={{
          ...pagination,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        onChange={handleTableChange}
      />

      <Modal
        title="创建报表"
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="报表名称"
            rules={[{ required: true, message: '请输入报表名称' }]}
          >
            <Input placeholder="例如: 月度销售报表" />
          </Form.Item>

          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="可选描述信息" />
          </Form.Item>

          <Form.Item
            name="data_source_id"
            label="数据源"
            rules={[{ required: true, message: '请选择数据源' }]}
          >
            <Select placeholder="请选择数据源">
              {dataSources.map((ds) => (
                <Select.Option key={ds.id} value={ds.id}>
                  {ds.name} ({ds.db_type})
                </Select.Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item name="output_formats" label="输出格式">
            <Select mode="multiple" placeholder="选择输出格式">
              <Select.Option value="excel">Excel</Select.Option>
              <Select.Option value="html">HTML</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item name="is_active" label="状态" valuePropName="checked">
            <Select>
              <Select.Option value={true}>启用</Select.Option>
              <Select.Option value={false}>禁用</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
