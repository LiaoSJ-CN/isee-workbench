import { useState, useEffect } from 'react';
import { Table, Button, Space, Modal, Form, Input, Select, message, Popconfirm, Tag } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, SyncOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { DataSource, DataSourceCreate } from '../types';
import { dataSourceApi } from '../api';

const { TextArea } = Input;

export default function DataSourceList() {
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingSource, setEditingSource] = useState<DataSource | null>(null);
  const [form] = Form.useForm<DataSourceCreate>();
  const [testingId, setTestingId] = useState<number | null>(null);
  const [dbType, setDbType] = useState<string>('postgresql');

  const loadDataSources = async () => {
    setLoading(true);
    try {
      const data = await dataSourceApi.list();
      setDataSources(data);
    } catch {
      message.error('加载数据源失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDataSources();
  }, []);

  const handleCreate = () => {
    setEditingSource(null);
    form.resetFields();
    setModalVisible(true);
  };

  const handleEdit = (source: DataSource) => {
    setEditingSource(source);
    form.setFieldsValue({
      ...source,
      password: '',
    });
    setModalVisible(true);
  };

  const handleDelete = async (id: number) => {
    try {
      await dataSourceApi.delete(id);
      message.success('删除成功');
      loadDataSources();
    } catch {
      message.error('删除失败');
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (editingSource) {
        await dataSourceApi.update(editingSource.id, values);
        message.success('更新成功');
      } else {
        await dataSourceApi.create(values);
        message.success('创建成功');
      }
      setModalVisible(false);
      loadDataSources();
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    }
  };

  const handleTest = async (id: number) => {
    setTestingId(id);
    try {
      const result = await dataSourceApi.test(id);
      if (result.success) {
        message.success({ content: `连接成功: ${result.version}`, duration: 5 });
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || '连接失败');
    } finally {
      setTestingId(null);
    }
  };

  const columns: ColumnsType<DataSource> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 150,
    },
    {
      title: '类型',
      dataIndex: 'db_type',
      key: 'db_type',
      width: 100,
      render: (type) => <Tag color="blue">{type}</Tag>,
    },
    {
      title: '主机',
      dataIndex: 'host',
      key: 'host',
      width: 150,
    },
    {
      title: '端口',
      dataIndex: 'port',
      key: 'port',
      width: 80,
    },
    {
      title: '数据库',
      dataIndex: 'database',
      key: 'database',
      width: 120,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            icon={<SyncOutlined spin={testingId === record.id} />}
            onClick={() => handleTest(record.id)}
            loading={testingId === record.id}
          >
            测试
          </Button>
          <Button type="link" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
            编辑
          </Button>
          <Popconfirm title="确定删除?" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" danger icon={<DeleteOutlined />}>
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
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          添加数据源
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={dataSources}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 10 }}
      />

      <Modal
        title={editingSource ? '编辑数据源' : '添加数据源'}
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="例如: 生产数据库" />
          </Form.Item>

          <Form.Item
            name="db_type"
            label="数据库类型"
            rules={[{ required: true, message: '请选择类型' }]}
          >
            <Select onChange={(v) => setDbType(v)}>
              <Select.Option value="sqlite">SQLite (本地文件)</Select.Option>
              <Select.Option value="postgresql">PostgreSQL</Select.Option>
              <Select.Option value="opengauss">OpenGauss</Select.Option>
              <Select.Option value="dws">DWS</Select.Option>
            </Select>
          </Form.Item>

          {dbType !== 'sqlite' && (
            <Space style={{ width: '100%' }} size="large">
              <Form.Item
                name="host"
                label="主机"
                rules={[{ required: dbType !== 'sqlite', message: '请输入主机' }]}
                style={{ flex: 1 }}
              >
                <Input placeholder="localhost 或 IP 地址" />
              </Form.Item>

              <Form.Item
                name="port"
                label="端口"
                rules={[{ required: dbType !== 'sqlite', message: '请输入端口' }]}
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
                rules={[{ required: dbType !== 'sqlite', message: '请输入用户名' }]}
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
