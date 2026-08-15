import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Space, Card, message, Descriptions, Tag, Table, Spin, Alert } from 'antd';
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  ReloadOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import { useReport, useReportPreviewHtml, useDownloadReport } from '../queries/useReports';
import { useEnqueueReportJob, useJobStatus } from '../queries/useJobs';
import { useReportParameters } from '../queries/useParameters';
import { TableSkeleton } from '../components/Skeleton';
import { ReportParameterForm } from '../components/ReportParameterForm';
import { formatError } from '../utils/error';

export default function ReportPreview() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const reportId = id ? Number(id) : null;

  const { data: report, isPending: loading } = useReport(reportId);
  const { data: parameters = [] } = useReportParameters(reportId);
  // Lazy preview query: enabled flips to true only after the user clicks
  // "刷新预览" / "生成预览". Each click re-fires the query and produces
  // fresh HTML.
  const [shouldFetch, setShouldFetch] = useState(false);
  const previewQ = useReportPreviewHtml(reportId, shouldFetch);
  const downloadReport = useDownloadReport();

  // ---- Excel async export (批 3b) ----------------------------------------
  // Flow: click "导出 Excel" → enqueue → poll job status → on done, show
  // a "下载" button that hits the existing `/reports/{id}/export/excel`
  // endpoint. The job id is kept in local state (not the URL) so a page
  // refresh resets to a clean slate — that's the right trade-off because
  // job rows are transient and the in-flight status would re-fire from
  // scratch anyway.
  const [excelJobId, setExcelJobId] = useState<number | null>(null);
  const enqueueExcel = useEnqueueReportJob(reportId);
  const excelJob = useJobStatus(excelJobId);

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

  const handleExportHtml = (format: 'html') => {
    if (!report) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 15);
    const filename = `${report.name}_${timestamp}.${format}`;
    downloadReport.mutate(
      { reportId: report.id, format, filename },
      {
        onSuccess: () => message.success(`${format.toUpperCase()} 导出成功`),
        onError: (err) => message.error(formatError(err, '导出失败')),
      },
    );
  };

  const handleExportExcel = (paramValues?: Record<string, unknown>) => {
    if (!report) return;
    enqueueExcel.mutate(
      { parameters: paramValues ?? {} },
      {
        onSuccess: (job) => setExcelJobId(job.id),
        onError: (err) => message.error(formatError(err, '导出任务提交失败')),
      },
    );
  };

  const handleDownloadExcel = () => {
    if (!report) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 15);
    const filename = `${report.name}_${timestamp}.xlsx`;
    message.loading({ content: '正在准备下载…', key: 'excel-download' });
    downloadReport.mutate(
      { reportId: report.id, format: 'excel', filename },
      {
        onSuccess: () => message.success({ content: 'Excel 下载成功', key: 'excel-download' }),
        onError: (err) =>
          message.error({ content: formatError(err, '下载失败'), key: 'excel-download' }),
      },
    );
  };

  if (loading) return <div style={{ padding: 24 }}><TableSkeleton rows={8} columns={4} /></div>;
  if (!report) return <div style={{ padding: 24 }}>报表不存在</div>;

  const excelStatus = excelJob.data?.status;
  const excelInFlight = excelStatus === 'pending' || excelStatus === 'running';
  const excelDone = excelStatus === 'done';
  const excelFailed = excelStatus === 'failed';

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
          {/* Toolbar shortcut only when there are no parameters — otherwise
              the form (rendered below) owns the submit button. */}
          {parameters.length === 0 && (
            <Button
              icon={<DownloadOutlined />}
              loading={enqueueExcel.isPending}
              disabled={excelInFlight}
              onClick={() => handleExportExcel()}
            >
              导出 Excel
            </Button>
          )}
          <Button icon={<DownloadOutlined />} onClick={() => handleExportHtml('html')}>
            导出 HTML
          </Button>
        </Space>
      </div>

      {parameters.length > 0 && (
        <Card size="small" style={{ marginBottom: 16 }} title="运行参数">
          <ReportParameterForm
            parameters={parameters}
            onSubmit={handleExportExcel}
            loading={enqueueExcel.isPending}
            submitLabel="导出 Excel"
          />
        </Card>
      )}

      {excelJobId !== null && (
        <Card size="small" style={{ marginBottom: 16 }} title="Excel 导出任务">
          <Space size="middle" align="center">
            {excelInFlight && <Spin size="small" />}
            {excelStatus === 'pending' && <Tag color="blue">排队中</Tag>}
            {excelStatus === 'running' && <Tag color="processing">执行中</Tag>}
            {excelDone && <Tag color="success">已完成</Tag>}
            {excelFailed && <Tag color="error">失败</Tag>}
            {excelDone && (
              <Button
                type="primary"
                size="small"
                icon={<DownloadOutlined />}
                loading={downloadReport.isPending}
                onClick={handleDownloadExcel}
              >
                下载 Excel
              </Button>
            )}
            <Button
              size="small"
              icon={<CloseCircleOutlined />}
              onClick={() => setExcelJobId(null)}
            >
              {excelDone || excelFailed ? '关闭' : '取消关注'}
            </Button>
          </Space>
          {excelFailed && excelJob.data?.error && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message="导出失败"
              description={excelJob.data.error}
            />
          )}
        </Card>
      )}

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