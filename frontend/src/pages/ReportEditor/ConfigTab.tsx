import { Card, Space, Form, Input, Select } from 'antd';
import type { Report, DataSource, ReportVisibility } from '../../types';

export interface ConfigTabProps {
  buffer: Report;
  dataSources: DataSource[];
  onBufferChange: (next: Report) => void;
}

export function ConfigTab({ buffer, dataSources, onBufferChange }: ConfigTabProps) {
  // 批 9.4 — visibility defaults to 'private' for new reports; the
  // backend's `ReportUpdate` accepts only 'public' | 'private', so we
  // mirror the literal at the UI level. Existing reports come in
  // with `visibility` undefined for pre-9.4 rows — fall back to
  // 'private' so the radio doesn't trip on undefined.
  const visibility: ReportVisibility = buffer.visibility ?? 'private';
  return (
    <Card title="基本配置">
      <Space direction="vertical" style={{ width: '100%' }} size="large">
        <Space style={{ width: '100%' }}>
          <Form.Item label="报表名称" style={{ flex: 1, margin: 0 }}>
            <Input
              value={buffer.name}
              onChange={(e) => onBufferChange({ ...buffer, name: e.target.value })}
            />
          </Form.Item>
          <Form.Item label="数据源" style={{ width: 200, margin: 0 }}>
            <Select
              value={buffer.data_source_id}
              onChange={(v) => onBufferChange({ ...buffer, data_source_id: v })}
              options={dataSources.map((ds) => ({
                value: ds.id,
                label: ds.name,
              }))}
            />
          </Form.Item>
          <Form.Item label="状态" style={{ width: 100, margin: 0 }}>
            <Select
              value={buffer.is_active}
              onChange={(v) => onBufferChange({ ...buffer, is_active: v })}
              options={[
                { value: true, label: '启用' },
                { value: false, label: '禁用' },
              ]}
            />
          </Form.Item>
        </Space>
        <Space style={{ width: '100%' }}>
          <Form.Item label="可见性" style={{ width: 240, margin: 0 }}>
            <Select
              value={visibility}
              onChange={(v: ReportVisibility) => onBufferChange({ ...buffer, visibility: v })}
              options={[
                { value: 'private', label: '私有（仅 owner/授权可读）' },
                { value: 'public', label: '公开（任意登录用户可读）' },
              ]}
            />
          </Form.Item>
        </Space>
        <Form.Item label="描述" style={{ margin: 0 }}>
          <Input.TextArea
            value={buffer.description || ''}
            onChange={(e) => onBufferChange({ ...buffer, description: e.target.value })}
            rows={2}
          />
        </Form.Item>
      </Space>
    </Card>
  );
}