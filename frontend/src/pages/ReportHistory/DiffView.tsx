import { ArrowLeftOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Drawer, Empty, Select, Space, Spin, Table, Typography } from 'antd';
import { useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useReport } from '../../queries/useReports';
import {
  useReportVersion,
  useReportVersionDiff,
  useReportVersions,
} from '../../queries/useReportVersions';
import type { FieldChange } from '../../types';

export default function ReportHistoryDiffPage() {
  const { id, vid } = useParams<{ id: string; vid: string }>();
  const reportId = id ? Number(id) : null;
  const versionId = vid ? Number(vid) : null;
  const [searchParams, setSearchParams] = useSearchParams();
  const againstParam = searchParams.get('against') ?? 'current';
  const againstValue: number | 'current' =
    againstParam === 'current' || againstParam === null ? 'current' : Number(againstParam);

  const navigate = useNavigate();
  const { data: report } = useReport(reportId);
  const { data: version } = useReportVersion(reportId, versionId);
  const { data: versions = [] } = useReportVersions(reportId);
  const { data: diff, isPending } = useReportVersionDiff(reportId, versionId, againstValue);

  const [showFullSnapshot, setShowFullSnapshot] = useState(false);

  if (!report || !version) {
    return <Spin style={{ display: 'block', margin: 80 }} />;
  }

  const handleAgainstChange = (val: number | 'current') => {
    setSearchParams(val === 'current' ? {} : { against: String(val) });
  };

  const renderChanges = (changes: FieldChange[]) => (
    <Table
      size="small"
      pagination={false}
      dataSource={changes}
      rowKey={(c) => c.field}
      columns={[
        { title: '字段', dataIndex: 'field', width: 200 },
        {
          title: '旧值',
          dataIndex: 'old_value',
          render: (v) => <code>{JSON.stringify(v)}</code>,
        },
        {
          title: '新值',
          dataIndex: 'new_value',
          render: (v) => <code>{JSON.stringify(v)}</code>,
        },
      ]}
    />
  );

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate(`/reports/${reportId}/history`)}
        >
          返回历史
        </Button>
        <Typography.Title level={3} style={{ margin: 0 }}>
          版本 v{version.version_number}
          {againstValue === 'current' ? ' vs 当前' : ` vs v${againstValue}`}
        </Typography.Title>
      </Space>

      <Card style={{ marginBottom: 16 }} title="对比目标">
        <Select
          value={againstValue}
          onChange={handleAgainstChange}
          style={{ width: 200 }}
          options={[
            { value: 'current', label: '当前 live' },
            ...versions
              .filter((v) => v.id !== versionId)
              .map((v) => ({ value: v.id, label: `v${v.version_number}` })),
          ]}
        />
      </Card>

      {isPending || !diff ? (
        <Spin />
      ) : (
        <>
          <Card title={`报表字段差异 (${diff.report_changes.length})`} style={{ marginBottom: 16 }}>
            {diff.report_changes.length === 0 ? (
              <Empty description="无字段变更" />
            ) : (
              renderChanges(diff.report_changes)
            )}
          </Card>

          <Card
            title={`报表项差异 (新增 ${diff.items_added.length} / 删除 ${diff.items_removed.length} / 修改 ${diff.items_modified.length})`}
            style={{ marginBottom: 16 }}
          >
            {diff.items_added.length === 0 &&
            diff.items_removed.length === 0 &&
            diff.items_modified.length === 0 ? (
              <Empty description="无变更" />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                {diff.items_added.map((i) => (
                  <Alert
                    key={i.id}
                    type="success"
                    showIcon
                    message={`+ 新增: ${i.name} (${i.item_type})`}
                  />
                ))}
                {diff.items_removed.map((i) => (
                  <Alert
                    key={i.id}
                    type="error"
                    showIcon
                    message={`- 删除: ${i.name} (${i.item_type})`}
                  />
                ))}
                {diff.items_modified.map((m) => (
                  <Card key={m.name} size="small" title={`~ 修改: ${m.name}`}>
                    {renderChanges(m.changes)}
                  </Card>
                ))}
              </Space>
            )}
          </Card>

          <Card
            title={`参数差异 (新增 ${diff.parameters_added.length} / 删除 ${diff.parameters_removed.length} / 修改 ${diff.parameters_modified.length})`}
            style={{ marginBottom: 16 }}
          >
            {diff.parameters_added.length === 0 &&
            diff.parameters_removed.length === 0 &&
            diff.parameters_modified.length === 0 ? (
              <Empty description="无变更" />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                {diff.parameters_added.map((p) => (
                  <Alert key={p.id} type="success" showIcon message={`+ 新增: ${p.name}`} />
                ))}
                {diff.parameters_removed.map((p) => (
                  <Alert key={p.id} type="error" showIcon message={`- 删除: ${p.name}`} />
                ))}
                {diff.parameters_modified.map((m) => (
                  <Card key={m.name} size="small" title={`~ 修改: ${m.name}`}>
                    {renderChanges(m.changes)}
                  </Card>
                ))}
              </Space>
            )}
          </Card>

          <Button onClick={() => setShowFullSnapshot(true)}>查看完整快照</Button>
        </>
      )}

      <Drawer
        title="完整快照"
        open={showFullSnapshot}
        onClose={() => setShowFullSnapshot(false)}
        width={720}
      >
        <pre style={{ fontSize: 12 }}>{JSON.stringify(version, null, 2)}</pre>
      </Drawer>
    </div>
  );
}
