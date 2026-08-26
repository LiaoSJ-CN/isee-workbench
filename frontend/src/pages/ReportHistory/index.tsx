import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button, Card, Empty, Space, Spin, Typography, message } from 'antd';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useReport } from '../../queries/useReports';
import { useDeleteReportVersion, useReportVersions } from '../../queries/useReportVersions';
import type { ReportVersionSummary } from '../../types';
import { RestoreConfirmModal } from './RestoreConfirmModal';
import { VersionTable } from './VersionTable';

export default function ReportHistoryPage() {
  const { id } = useParams<{ id: string }>();
  const reportId = id ? Number(id) : null;
  const navigate = useNavigate();
  const { data: report, isPending: reportLoading } = useReport(reportId);
  const { data: versions = [], isPending: versionsLoading } = useReportVersions(reportId);
  const deleteMutation = useDeleteReportVersion(reportId ?? 0);
  const [restoreTarget, setRestoreTarget] = useState<ReportVersionSummary | null>(null);

  if (reportLoading || versionsLoading) {
    return <Spin style={{ display: 'block', margin: 80 }} />;
  }
  if (!report) {
    return <div style={{ padding: 24 }}>报表不存在</div>;
  }

  const handleDelete = async (v: ReportVersionSummary) => {
    try {
      await deleteMutation.mutateAsync(v.id);
      message.success(`已删除 v${v.version_number}`);
    } catch {
      message.error('删除失败');
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/reports/${reportId}`)}>
          返回报表
        </Button>
        <Typography.Title level={3} style={{ margin: 0 }}>
          报表历史 — {report.name}
        </Typography.Title>
      </Space>

      <Card>
        {versions.length === 0 ? (
          <Empty
            description={
              <span>
                还没有历史版本。
                <br />在{' '}
                <Button type="link" onClick={() => navigate(`/reports/${reportId}/edit`)}>
                  编辑器
                </Button>{' '}
                里点击「保存为版本」开始记录。
              </span>
            }
          />
        ) : (
          <VersionTable
            reportId={reportId!}
            report={report}
            versions={versions}
            onRestore={setRestoreTarget}
            onDelete={handleDelete}
          />
        )}
      </Card>

      <RestoreConfirmModal
        open={restoreTarget !== null}
        reportId={reportId!}
        version={restoreTarget}
        currentUpdatedAt={report.updated_at ?? null}
        onClose={() => setRestoreTarget(null)}
        onRestored={() => navigate(`/reports/${reportId}`)}
      />
    </div>
  );
}
