import { Card, Button, Table, Tag, Space, Popconfirm } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import type { ReportParameter, ParameterType } from '../../types';

export interface ParametersTabProps {
  parameters: ReportParameter[];
  onAdd: () => void;
  onEdit: (parameter: ReportParameter) => void;
  onDelete: (paramId: number) => void;
}

export function ParametersTab({ parameters, onAdd, onEdit, onDelete }: ParametersTabProps) {
  return (
    <Card
      title="运行参数"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>
          添加参数
        </Button>
      }
    >
      <p style={{ color: '#999', marginBottom: 16 }}>
        报表运行参数，用户在预览页可填写。例如：<code>{'{region}'}</code> / <code>{'{start_date}'}</code>。
      </p>
      {parameters.length > 0 ? (
        <Table<ReportParameter>
          dataSource={parameters}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name', key: 'name', width: 160 },
            { title: '标签', dataIndex: 'label', key: 'label', width: 160 },
            {
              title: '类型',
              dataIndex: 'type',
              key: 'type',
              width: 100,
              render: (t: ParameterType) => <Tag color="blue">{t}</Tag>,
            },
            {
              title: '必填',
              dataIndex: 'required',
              key: 'required',
              width: 80,
              render: (r: boolean) => (r ? '是' : '否'),
            },
            {
              title: '默认值',
              dataIndex: 'default',
              key: 'default',
              render: (d: unknown) =>
                d === null || d === undefined ? <span style={{ color: '#999' }}>-</span> : String(d),
            },
            {
              title: '选项',
              dataIndex: 'options',
              key: 'options',
              render: (opts: string[] | null) =>
                opts && opts.length > 0
                  ? opts.map((o) => <Tag key={o}>{o}</Tag>)
                  : <span style={{ color: '#999' }}>-</span>,
            },
            {
              title: '操作',
              key: 'action',
              width: 160,
              render: (_: unknown, record: ReportParameter) => (
                <Space size="small">
                  <Button
                    type="link"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => onEdit(record)}
                  >
                    编辑
                  </Button>
                  <Popconfirm
                    title="确定删除该参数？"
                    onConfirm={() => onDelete(record.id)}
                  >
                    <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      ) : (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
          暂无参数，点击上方按钮添加
        </div>
      )}
    </Card>
  );
}