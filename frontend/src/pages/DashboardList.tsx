/** Dashboard list page (批 14.3).
 *
 * Mirrors :component:`ReportList` — tableLayout="fixed", scroll x:1200,
 * visibility filter, and the same "操作" column with 详情 / 编辑 / 复制 /
 * 删除. The Dashboard domain is owner+visibility+shares (vs report's
 * owner-only), so the visibility tag uses the dashboard's value rather
 * than a derived "private/public" inference.
 */

import { useMemo, useState } from 'react';
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';

import { dashboardApi } from '../api';
import type { Dashboard, DashboardVisibility } from '../types';

const { Title } = Typography;

const VISIBILITY_OPTIONS: { value: DashboardVisibility | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'public', label: '公开' },
  { value: 'org', label: '部门' },
  { value: 'private', label: '私有' },
];

export default function DashboardList() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [visibilityFilter, setVisibilityFilter] = useState<DashboardVisibility | 'all'>('all');
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<{ name: string; description?: string; visibility: DashboardVisibility }>();

  const dashboardsQuery = useQuery({
    queryKey: ['dashboards'],
    queryFn: () => dashboardApi.list(),
  });

  const createMut = useMutation({
    mutationFn: (values: { name: string; description?: string; visibility: DashboardVisibility }) =>
      dashboardApi.create(values),
    onSuccess: (created) => {
      message.success('看板已创建');
      queryClient.invalidateQueries({ queryKey: ['dashboards'] });
      setCreateOpen(false);
      createForm.resetFields();
      navigate(`/dashboards/${created.id}/edit`);
    },
    onError: (err: Error) => message.error(`创建失败: ${err.message}`),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => dashboardApi.delete(id),
    onSuccess: () => {
      message.success('看板已删除');
      queryClient.invalidateQueries({ queryKey: ['dashboards'] });
    },
    onError: (err: Error) => message.error(`删除失败: ${err.message}`),
  });

  const duplicateMut = useMutation({
    mutationFn: (id: number) => dashboardApi.duplicate(id),
    onSuccess: (created) => {
      message.success('看板已复制');
      queryClient.invalidateQueries({ queryKey: ['dashboards'] });
      navigate(`/dashboards/${created.id}/edit`);
    },
    onError: (err: Error) => message.error(`复制失败: ${err.message}`),
  });

  const filtered = useMemo(() => {
    const all = dashboardsQuery.data ?? [];
    return all.filter((d) => {
      if (visibilityFilter !== 'all' && d.visibility !== visibilityFilter) return false;
      if (search.trim() && !d.name.toLowerCase().includes(search.trim().toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [dashboardsQuery.data, visibilityFilter, search]);

  const visibilityTag = (v: DashboardVisibility) => {
    if (v === 'public') return <Tag color="green">公开</Tag>;
    if (v === 'org') return <Tag color="blue">部门</Tag>;
    return <Tag color="default">私有</Tag>;
  };

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          看板
        </Title>
        <Space>
          <Input.Search
            placeholder="搜索看板名称"
            allowClear
            style={{ width: 200 }}
            onChange={(e) => setSearch(e.target.value)}
            onSearch={setSearch}
          />
          <Select
            value={visibilityFilter}
            options={VISIBILITY_OPTIONS}
            style={{ width: 120 }}
            onChange={setVisibilityFilter}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建看板
          </Button>
        </Space>
      </Space>

      <Table<Dashboard>
        rowKey="id"
        loading={dashboardsQuery.isLoading}
        dataSource={filtered}
        tableLayout="fixed"
        scroll={{ x: 1200 }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        columns={[
          {
            title: 'ID',
            dataIndex: 'id',
            width: 80,
          },
          {
            title: '名称',
            dataIndex: 'name',
            width: 280,
            render: (name: string, row: Dashboard) => (
              <Link to={`/dashboards/${row.id}`}>{name}</Link>
            ),
          },
          {
            title: '可见性',
            dataIndex: 'visibility',
            width: 100,
            render: (v: DashboardVisibility) => visibilityTag(v),
          },
          {
            title: '所有者',
            dataIndex: 'owner_username',
            width: 140,
            render: (u?: string | null) => u ?? '-',
          },
          {
            title: '项数',
            dataIndex: 'item_count',
            width: 80,
            render: (n?: number | null) => n ?? 0,
          },
          {
            title: '更新时间',
            dataIndex: 'updated_at',
            width: 180,
            render: (s: string) => new Date(s).toLocaleString(),
          },
          {
            title: '操作',
            width: 260,
            fixed: 'right',
            render: (_: unknown, row: Dashboard) => (
              <Space size="small">
                <Button
                  size="small"
                  type="link"
                  icon={<EyeOutlined />}
                  onClick={() => navigate(`/dashboards/${row.id}`)}
                >
                  详情
                </Button>
                <Button
                  size="small"
                  type="link"
                  icon={<EditOutlined />}
                  onClick={() => navigate(`/dashboards/${row.id}/edit`)}
                >
                  编辑
                </Button>
                <Button
                  size="small"
                  type="link"
                  icon={<CopyOutlined />}
                  loading={duplicateMut.isPending && duplicateMut.variables === row.id}
                  onClick={() => duplicateMut.mutate(row.id)}
                >
                  复制
                </Button>
                <Popconfirm
                  title="确认删除该看板？"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => deleteMut.mutate(row.id)}
                >
                  <Button
                    size="small"
                    type="link"
                    danger
                    icon={<DeleteOutlined />}
                    loading={deleteMut.isPending && deleteMut.variables === row.id}
                  >
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="新建看板"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        confirmLoading={createMut.isPending}
        okText="创建并编辑"
        cancelText="取消"
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={{ visibility: 'private' }}
          onFinish={(values) => createMut.mutate(values)}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入看板名称' }]}>
            <Input placeholder="例：运营日报看板" />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea rows={3} placeholder="可选" />
          </Form.Item>
          <Form.Item label="可见性" name="visibility" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'private', label: '私有（仅自己 + 分享用户）' },
                { value: 'org', label: '部门（同部门成员可读）' },
                { value: 'public', label: '公开（所有用户可读）' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
