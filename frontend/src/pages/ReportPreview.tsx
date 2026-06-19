import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Space, Card, message, Spin, Descriptions, Tag, Table } from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import type { Report } from '../types';
import { reportApi } from '../api';

export default function ReportPreview() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<Report | null>(null);
  const [htmlContent, setHtmlContent] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (id) {
      loadReport();
    }
  }, [id]);

  const loadReport = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const data = await reportApi.get(Number(id));
      setReport(data);
    } catch {
      message.error('加载报表失败');
    } finally {
      setLoading(false);
    }
  };

  const handlePreview = async () => {
    if (!id) return;
    setGenerating(true);
    try {
      const result = await reportApi.preview(Number(id), 'html');
      if (result.preview_data) {
        setHtmlContent(result.preview_data as string);
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || '预览生成失败');
    } finally {
      setGenerating(false);
    }
  };

  const handleExport = async (format: 'excel' | 'html') => {
    if (!report) return;
    try {
      const result = await reportApi.generate(report.id, format);
      if (result.success && result.file_path) {
        window.open(reportApi.getExportUrl(report.id, format), '_blank');
        message.success(`${format.toUpperCase()} 导出成功`);
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || '导出失败');
    }
  };

  if (loading) return <div style={{ padding: 24, textAlign: 'center' }}><Spin size="large" /></div>;
  if (!report) return <div style={{ padding: 24 }}>报表不存在</div>;

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/reports/${report?.id}`)}>
            返回
          </Button>
          <h2 style={{ margin: 0 }}>{report.name} - 预览</h2>
        </Space>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            loading={generating}
            onClick={handlePreview}
          >
            刷新预览
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport('excel')}>
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
        {htmlContent ? (
          <iframe
            srcDoc={htmlContent}
            style={{
              width: '100%',
              height: '600px',
              border: '1px solid #d9d9d9',
              borderRadius: 4
            }}
            title="Report Preview"
          />
        ) : (
          <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>
            <p>点击「刷新预览」按钮生成预览</p>
            <Button type="primary" onClick={handlePreview} loading={generating}>
              生成预览
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
