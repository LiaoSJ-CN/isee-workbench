import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Space, Card, message, Descriptions, Tag, Table } from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { useReport, useReportPreviewHtml, useDownloadReport } from '../queries/useReports';
import { TableSkeleton } from '../components/Skeleton';

export default function ReportPreview() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const reportId = id ? Number(id) : null;

  const { data: report, isPending: loading } = useReport(reportId);
  // Lazy preview query: enabled flips to true only after the user clicks
  // "刷新预览" / "生成预览". Each click re-fires the query and produces
  // fresh HTML.
  const [shouldFetch, setShouldFetch] = useState(false);
  const previewQ = useReportPreviewHtml(reportId, shouldFetch);
  const downloadReport = useDownloadReport();

  // Blob URL lifecycle: when the preview string arrives, wrap it in a
  // blob URL; revoke the previous one when the string changes.
  const [previewSrc, setPreviewSrc] = useState<string>('');
  useEffect(() => {
    if (!previewQ.data) return;
    const blob = new Blob([previewQ.data], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    setPreviewSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [previewQ.data]);

  const handlePreview = () => setShouldFetch(true);

  const handleExport = (format: 'excel' | 'html') => {
    if (!report) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 15);
    const filename = `${report.name}_${timestamp}.${format}`;
    downloadReport.mutate(
      { reportId: report.id, format, filename },
      {
        onSuccess: () => message.success(`${format.toUpperCase()} 导出成功`),
        onError: (err) => {
          const error = err as { response?: { data?: { detail?: string } } };
          message.error(error.response?.data?.detail || '导出失败');
        },
      },
    );
  };

  if (loading) return <div style={{ padding: 24 }}><TableSkeleton rows={8} columns={4} /></div>;
  if (!report) return <div style={{ padding: 24 }}>报表不存在</div>;

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/reports/${report.id}`)}>
            返回
          </Button>
          <h2 style={{ margin: 0 }}>{report.name} - 预览</h2>
        </Space>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            loading={previewQ.isFetching}
            onClick={handlePreview}
          >
            刷新预览
          </Button>
          <Button
            icon={<DownloadOutlined />}
            loading={downloadReport.isPending}
            onClick={() => handleExport('excel')}
          >
            导出 Excel
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport('html')}>
            导出 HTML
          </Button>
        </Space>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <Descriptions column={4} size="small">
          <Descriptions.Item label="名称">{report.name}</Descriptions.Item>
          <Descriptions.Item label="数据源 ID">{report.data_source_id}</Descriptions.Item>
          <Descriptions.Item label="报表项">{report.items?.length || 0}</Descriptions.Item>
          <Descriptions.Item label="状态">
            {report.is_active ? <Tag color="green">启用</Tag> : <Tag>禁用</Tag>}
          </Descriptions.Item>
          {report.description && (
            <Descriptions.Item label="描述" span={4}>{report.description}</Descriptions.Item>
          )}
        </Descriptions>
      </Card>

      {report.items && report.items.length > 0 && (
        <Card title="报表项配置" style={{ marginBottom: 16 }}>
          <Table
            dataSource={report.items}
            rowKey="id"
            size="small"
            pagination={false}
            columns={[
              { title: '名称', dataIndex: 'name', key: 'name' },
              { title: '类型', dataIndex: 'item_type', key: 'item_type' },
              { title: '表名', dataIndex: 'table_name', key: 'table_name' },
              { title: '字段', dataIndex: 'fields', key: 'fields', render: (f) => f?.join(', ') || '-' },
              {
                title: '查询条件',
                dataIndex: 'where_conditions',
                key: 'where_conditions',
                render: (conds) => conds?.length || 0,
              },
            ]}
          />
        </Card>
      )}

      <Card title="HTML 预览">
        {previewSrc ? (
          <iframe
            src={previewSrc}
            sandbox="allow-scripts"
            style={{
              width: '100%',
              height: '2400px',
              border: '1px solid #d9d9d9',
              borderRadius: 4
            }}
            title="Report Preview"
          />
        ) : (
          <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>
            <p>点击「刷新预览」按钮生成预览</p>
            <Button type="primary" onClick={handlePreview} loading={previewQ.isFetching}>
              生成预览
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
