import { useState } from 'react';
import { Table, Button, Space, Modal, Form, Input, Select, message, Popconfirm, Tag, Alert } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, SyncOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { DataSource, DataSourceCreate } from '../types';
import { formatError } from '../utils/error';
import {
  useCreateDataSource,
  useDataSources,
  useDeleteDataSource,
  useTestDataSource,
  useUpdateDataSource,
} from '../queries/useDataSources';

const { TextArea } = Input;

export default function DataSourceList() {
  const { data: dataSources = [], isPending } = useDataSources();
  const createDs = useCreateDataSource();
  const updateDs = useUpdateDataSource();
  const deleteDs = useDeleteDataSource();
  const testDs = useTestDataSource();

  const [modalVisible, setModalVisible] = useState(false);
  const [editingSource, setEditingSource] = useState<DataSource | null>(null);
  const [form] = Form.useForm<DataSourceCreate>();
  const [dbType, setDbType] = useState<string>('postgresql');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });

  const handleCreate = () => {
    setEditingSource(null);
    form.resetFields();
    setDbType('postgresql');
    setModalVisible(true);
  };

  const handleEdit = (source: DataSource) => {
    setEditingSource(source);
    setDbType(source.db_type);
    form.setFieldsValue({ ...source, password: '' });
    setModalVisible(true);
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择要删除的数据源');
      return;
    }

    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>确定要删除选中的 {selectedRowKeys.length} 个数据源吗？</p>
          <Alert type="warning" message="删除后无法恢复，请谨慎操作！" />
        </div>
      ),
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        // Iterate rather than Promise.all so each delete surfaces its own
        // error message via the mutation's onError; partial failures are
        // visible instead of one rejected promise swallowing the rest.
        const ids = selectedRowKeys as number[];
        let succeeded = 0;
        for (const id of ids) {
          try {
            await deleteDs.mutateAsync(id);
            succeeded += 1;
          } catch {
            // Individual error already surfaced via onError; keep going.
          }
        }
        setSelectedRowKeys([]);
        if (succeeded > 0) message.success(`成功删除 ${succeeded} 个数据源`);
      },
    });
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (editingSource) {
        await updateDs.mutateAsync({ id: editingSource.id, payload: values });
        message.success('更新成功');
      } else {
        await createDs.mutateAsync(values);
        message.success('创建成功');
      }
      setModalVisible(false);
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    }
  };

  const handleTest = (id: number) => {
    testDs.mutate(id, {
      onSuccess: (result) => {
        if (result.success) {
          message.success({ content: `连接成功: ${result.version}`, duration: 5 });
        }
      },
      onError: (err) => {
        const error = err as { response?: { data?: { detail?: string } } };
        message.error(error.response?.data?.detail || '连接失败');
      },
    });
  };

  const handleDelete = (id: number) => {
    deleteDs.mutate(id, {
      onSuccess: () => message.success('删除成功'),
      onError: (err) => message.error(formatError(err, '删除失败')),
    });
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

  const columns: ColumnsType<DataSource> = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 150 },
    {
      title: '类型',
      dataIndex: 'db_type',
      key: 'db_type',
      width: 100,
      render: (type) => <Tag color="blue">{type}</Tag>,
    },
    { title: '主机', dataIndex: 'host', key: 'host', width: 120, render: (v) => v || '-' },
    { title: '端口', dataIndex: 'port', key: 'port', width: 80, render: (v) => v || '-' },
    { title: '数据库', dataIndex: 'database', key: 'database', width: 120, ellipsis: true },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<SyncOutlined spin={testDs.isPending && testDs.variables === record.id} />}
            onClick={() => handleTest(record.id)}
            loading={testDs.isPending && testDs.variables === record.id}
          >
            测试
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
            编辑
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
        <h2 style={{ margin: 0 }}>数据源管理</h2>
        <Space>
          <Button
            danger
            icon={<DeleteOutlined />}
            disabled={selectedRowKeys.length === 0}
            onClick={handleBatchDelete}
          >
            批量删除 {selectedRowKeys.length > 0 ? `(${selectedRowKeys.length})` : ''}
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            添加数据源
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={dataSources}
        rowKey="id"
        loading={isPending}
        rowSelection={rowSelection}
        scroll={{ x: 'max-content' }}
        pagination={{
          ...pagination,
          total: dataSources.length,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        onChange={handleTableChange}
      />

      <Modal
        title={editingSource ? '编辑数据源' : '添加数据源'}
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        confirmLoading={createDs.isPending || updateDs.isPending}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如: 生产数据库" />
          </Form.Item>

          <Form.Item
            name="db_type"
            label="数据库类型"
            rules={[{ required: true, message: '请选择类型' }]}
          >
            <Select
              onChange={(v) => setDbType(v)}
              options={[
                { value: 'sqlite', label: 'SQLite (本地文件)' },
                { value: 'postgresql', label: 'PostgreSQL' },
                { value: 'opengauss', label: 'OpenGauss' },
                { value: 'dws', label: 'DWS' },
              ]}
            />
          </Form.Item>

          {dbType !== 'sqlite' && (
            <Space style={{ width: '100%' }} size="large">
              <Form.Item
                name="host"
                label="主机"
                rules={[{ required: true, message: '请输入主机' }]}
                style={{ flex: 1 }}
              >
                <Input placeholder="localhost 或 IP 地址" />
              </Form.Item>

              <Form.Item
                name="port"
                label="端口"
                rules={[{ required: true, message: '请输入端口' }]}
                style={{ width: 100 }}
              >
                <Input type="number" placeholder="5432" />
              </Form.Item>
            </Space>
          )}

          <Space style={{ width: '100%' }} size="large">
            <Form.Item
              name="database"
              label={dbType === 'sqlite' ? '数据库文件路径' : '数据库名'}
              rules={[{ required: true, message: '请输入数据库名' }]}
              style={{ flex: 1 }}
            >
              <Input placeholder={dbType === 'sqlite' ? '/tmp/test.db' : 'database_name'} />
            </Form.Item>

            <Form.Item name="schema_name" label="Schema" style={{ flex: 1 }}>
              <Input placeholder="public (可选)" />
            </Form.Item>
          </Space>

          {dbType !== 'sqlite' && (
            <Space style={{ width: '100%' }} size="large">
              <Form.Item
                name="username"
                label="用户名"
                rules={[{ required: true, message: '请输入用户名' }]}
                style={{ flex: 1 }}
              >
                <Input placeholder="username" />
              </Form.Item>

              <Form.Item
                name="password"
                label={editingSource ? '密码 (不修改请留空)' : '密码'}
                style={{ flex: 1 }}
              >
                <Input.Password placeholder={editingSource ? '••••••••' : ''} />
              </Form.Item>
            </Space>
          )}

          <Form.Item name="description" label="描述">
            <TextArea rows={3} placeholder="可选描述信息" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
