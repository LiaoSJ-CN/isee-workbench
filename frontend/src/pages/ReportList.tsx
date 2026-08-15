import { useState } from 'react';
import { Table, Button, Space, Modal, Form, Input, Select, message, Popconfirm, Tag, Alert } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, PlayCircleOutlined, EyeOutlined, ClockCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import type { Report, ReportCreate } from '../types';
import { formatError } from '../utils/error';
import {
  useCreateReport,
  useDeleteReport,
  useDownloadReport,
  useGenerateReport,
  useReports,
} from '../queries/useReports';
import { useDataSources } from '../queries/useDataSources';

export default function ReportList() {
  const { data: reports = [], isPending } = useReports();
  const { data: dataSources = [] } = useDataSources();
  const createReport = useCreateReport();
  const deleteReport = useDeleteReport();
  const generateReport = useGenerateReport();
  const downloadReport = useDownloadReport();

  const [modalVisible, setModalVisible] = useState(false);
  const [form] = Form.useForm<ReportCreate>();
  const navigate = useNavigate();
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });

  const handleCreate = () => {
    form.resetFields();
    form.setFieldsValue({ output_formats: ['excel', 'html'], is_active: true });
    setModalVisible(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      await createReport.mutateAsync(values);
      message.success('创建成功');
      setModalVisible(false);
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    }
  };

  const handleBatchDelete = () => {
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
        const ids = selectedRowKeys as number[];
        let succeeded = 0;
        for (const id of ids) {
          try {
            await deleteReport.mutateAsync(id);
            succeeded += 1;
          } catch {
            // Individual error already surfaced via onError; keep going.
          }
        }
        setSelectedRowKeys([]);
        if (succeeded > 0) message.success(`成功删除 ${succeeded} 个报表`);
      },
    });
  };

  const handleDelete = (id: number) => {
    deleteReport.mutate(id, {
      onSuccess: () => message.success('删除成功'),
      onError: (err) => message.error(formatError(err, '删除失败')),
    });
  };

  const handleGenerate = (report: Report, format: 'excel' | 'html') => {
    message.loading({ content: '正在生成报表...', key: 'export' });
    generateReport.mutate(
      { reportId: report.id, outputFormat: format },
      {
        onSuccess: async () => {
          const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 15);
          const filename = `${report.name}_${timestamp}.${format}`;
          try {
            await downloadReport.mutateAsync({ reportId: report.id, format, filename });
            message.success({ content: `${format.toUpperCase()} 下载成功`, key: 'export' });
          } catch (err) {
            message.error({ content: formatError(err, '下载失败'), key: 'export' });
          }
        },
        onError: (err) => {
          message.error({ content: formatError(err, '生成失败'), key: 'export' });
        },
      },
    );
  };

  const handleTableChange = (pag: { current?: number; pageSize?: number }) => {
    setPagination({
      current: pag.current || 1,
      pageSize: pag.pageSize || 10,
    });
  };

  const rowSelection = {
    selectedRowKeys,
    onChange: (keys: React.Key[]) => setSelectedRowKeys(keys),
  };

  const columns: ColumnsType<Report> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name, record) => (
        <Button type="link" onClick={() => navigate(`/reports/${record.id}`)}>{name}</Button>
      ),
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
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
      render: (_, record) =>
        record.is_scheduled ? (
          <Tag icon={<ClockCircleOutlined />} color="green">
            {record.cron_expression || '已配置'}
          </Tag>
        ) : (
          <Tag>未配置</Tag>
        ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active) => (active ? <Tag color="green">启用</Tag> : <Tag color="gray">禁用</Tag>),
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
        loading={isPending}
        rowSelection={rowSelection}
        scroll={{ x: 'max-content' }}
        pagination={{
          ...pagination,
          total: reports.length,
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
        confirmLoading={createReport.isPending}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="报表名称" rules={[{ required: true, message: '请输入报表名称' }]}>
            <Input placeholder="例如: 月度销售报表" />
          </Form.Item>

          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="可选描述信息" />
          </Form.Item>

          <Form.Item name="data_source_id" label="数据源" rules={[{ required: true, message: '请选择数据源' }]}>
            <Select
              placeholder="请选择数据源"
              options={dataSources.map((ds) => ({
                value: ds.id,
                label: `${ds.name} (${ds.db_type})`,
              }))}
            />
          </Form.Item>

          <Form.Item name="output_formats" label="输出格式">
            <Select
              mode="multiple"
              placeholder="选择输出格式"
              options={[
                { value: 'excel', label: 'Excel' },
                { value: 'html', label: 'HTML' },
              ]}
            />
          </Form.Item>

          <Form.Item name="is_active" label="状态" valuePropName="checked">
            <Select
              options={[
                { value: true, label: '启用' },
                { value: false, label: '禁用' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
