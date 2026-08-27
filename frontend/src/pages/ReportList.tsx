import { useState } from 'react';
import {
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  Select,
  message,
  Popconfirm,
  Tag,
  Alert,
  Card,
  Spin,
  Dropdown,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  PlayCircleOutlined,
  EyeOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  DownloadOutlined,
  CloseCircleOutlined,
  ShareAltOutlined,
  BellOutlined,
  CopyOutlined,
  MoreOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import type { Report, ReportCreate } from '../types';
import { jobsApi } from '../api';
import { formatError } from '../utils/error';
import {
  useCreateReport,
  useDeleteReport,
  useDeleteReportShare,
  useDuplicateReport,
  useReportShares,
  useReports,
  useUpsertReportShare,
} from '../queries/useReports';
import { useDataSources, useUsers } from '../queries/useDataSources';
import { useJobStatus } from '../queries/useJobs';
import { useMe } from '../queries/useAuth';
import { ReportShareModal } from '../components/ReportShareModal';
import { SubscriptionModal } from '../components/SubscriptionModal';
// We don't use `useEnqueueReportJob` here — see handleGenerateExcel.

export default function ReportList() {
  const { data: reports = [], isPending } = useReports();
  const { data: dataSources = [] } = useDataSources();
  const createReport = useCreateReport();
  const deleteReport = useDeleteReport();
  const duplicateReport = useDuplicateReport();

  // ---- 批 9.4: share modal state ----
  // Owner-or-admin gate. The backend enforces the same; we just
  // hide the affordance so non-owners don't see a button that 404s.
  const me = useMe();
  const currentUserId = me.data?.user_id;
  const isAdmin = me.data?.role === 'admin';
  const [shareTarget, setShareTarget] = useState<Report | null>(null);
  const shares = useReportShares(shareTarget?.id ?? null);
  const upsertShare = useUpsertReportShare();
  const revokeShare = useDeleteReportShare();
  // User list for the share picker — only fetched when the modal opens.
  const usersQuery = useUsers({ enabled: shareTarget != null });

  const [modalVisible, setModalVisible] = useState(false);
  const [form] = Form.useForm<ReportCreate>();
  const navigate = useNavigate();
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });

  // ---- 批 8.3: subscription modal ----
  // The modal is opened from a row's "订阅" button and binds the
  // current user to the chosen report + cron + notification. The
  // list page is just the entry point — the heavy listing /
  // pause / resume / delete lives in /my-subscriptions (so the
  // ReportList row stays focused on per-report actions).
  const [subTarget, setSubTarget] = useState<Report | null>(null);

  // ---- Excel async export (批 8.5 / TODO-5) -----------------------------
  // ReportList is a navigation page — the user clicks Excel and usually
  // leaves. Following ReportPreview's pattern (批 3b + 批 8.5): enqueue →
  // poll → on done, hit /jobs/{id}/download to fetch the worker's file
  // without re-rendering. The in-flight job is surfaced as a card at the
  // top so the user sees progress and can pick up the download when
  // they come back.
  //
  // Single-slot state: if the user clicks Excel on row B while A is
  // still in flight, A's card disappears (worker keeps running, but the
  // UI loses the reference). That's the right trade-off for a list page
  // — ReportPreview owns "I want to wait for this one", ReportList owns
  // "fire-and-forget". The next batch can introduce a job-history
  // drawer if real users hit this race.
  const [excelJob, setExcelJob] = useState<{ jobId: number; report: Report } | null>(null);
  const excelStatus = useJobStatus(excelJob?.jobId ?? null);
  const [downloadingExcel, setDownloadingExcel] = useState(false);

  // ---- PDF async export (批 8.1) ------------------------------------------
  // Same single-slot pattern, independent of the Excel slot so a user
  // can fire both formats on the same report in parallel. Each format
  // owns one in-flight reference; clicking a different row's PDF while
  // one is pending will replace the visible card but the previous
  // worker keeps running in the background (the user can find it again
  // via /jobs/{id} polling at lower fidelity — not surfaced in this
  // page by design).
  const [pdfJob, setPdfJob] = useState<{ jobId: number; report: Report } | null>(null);
  const pdfStatus = useJobStatus(pdfJob?.jobId ?? null);
  const [downloadingPdf, setDownloadingPdf] = useState(false);

  const handleCreate = () => {
    form.resetFields();
    form.setFieldsValue({ output_formats: ['excel', 'html'], is_active: true });
    setModalVisible(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      await createReport.mutateAsync(values);
      message.success('创建成功');
      setModalVisible(false);
    } catch (err) {
      if (err instanceof Error) message.error(err.message);
    }
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择要删除的报表');
      return;
    }

    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>确定要删除选中的 {selectedRowKeys.length} 个报表吗？</p>
          <Alert type="warning" message="报表删除后无法恢复，请谨慎操作！" />
        </div>
      ),
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        const ids = selectedRowKeys as number[];
        let succeeded = 0;
        for (const id of ids) {
          try {
            await deleteReport.mutateAsync(id);
            succeeded += 1;
          } catch {
            // Individual error already surfaced via onError; keep going.
          }
        }
        setSelectedRowKeys([]);
        if (succeeded > 0) message.success(`成功删除 ${succeeded} 个报表`);
      },
    });
  };

  const handleDelete = (id: number) => {
    deleteReport.mutate(id, {
      onSuccess: () => message.success('删除成功'),
      onError: (err) => message.error(formatError(err, '删除失败')),
    });
  };

  const handleDuplicate = (report: Report) => {
    duplicateReport.mutate(
      { id: report.id },
      {
        onSuccess: (clone) => navigate(`/reports/${clone.id}`),
        onError: (err) => message.error(formatError(err, '复制失败')),
      },
    );
  };

  const handleGenerateExcel = async (report: Report) => {
    message.loading({ content: '正在提交导出任务…', key: 'export' });
    try {
      // Direct API call rather than `useEnqueueReportJob`: that hook
      // captures `reportId` in its mutationFn closure, which would be
      // null on the first click (we don't know the id until the server
      // returns it). Calling `jobsApi.enqueue` with the explicit
      // `report.id` sidesteps the closure problem and keeps the
      // loading flag local to this component.
      const job = await jobsApi.enqueue(report.id, {
        parameters: {},
        output_format: 'excel',
      });
      setExcelJob({ jobId: job.id, report });
      message.success({ content: `「${report.name}」导出任务已提交`, key: 'export' });
    } catch (err) {
      message.error({ content: formatError(err, '导出任务提交失败'), key: 'export' });
    }
  };

  const handleDownloadExcel = async () => {
    if (!excelJob) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 15);
    const filename = `${excelJob.report.name}_${timestamp}.xlsx`;
    message.loading({ content: '正在准备下载…', key: 'export' });
    setDownloadingExcel(true);
    try {
      await jobsApi.download(excelJob.jobId, filename);
      message.success({ content: 'Excel 下载成功', key: 'export' });
      // Slot is freed; next click starts a new job. Keep the row's
      // Excel button usable again immediately.
      setExcelJob(null);
    } catch (err) {
      message.error({ content: formatError(err, '下载失败'), key: 'export' });
    } finally {
      setDownloadingExcel(false);
    }
  };

  const handleGeneratePdf = async (report: Report) => {
    message.loading({ content: '正在提交 PDF 导出任务…', key: 'pdf-export' });
    try {
      const job = await jobsApi.enqueue(report.id, {
        parameters: {},
        output_format: 'pdf',
      });
      setPdfJob({ jobId: job.id, report });
      message.success({ content: `「${report.name}」PDF 导出任务已提交`, key: 'pdf-export' });
    } catch (err) {
      message.error({ content: formatError(err, 'PDF 导出任务提交失败'), key: 'pdf-export' });
    }
  };

  const handleDownloadPdf = async () => {
    if (!pdfJob) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 15);
    const filename = `${pdfJob.report.name}_${timestamp}.pdf`;
    message.loading({ content: '正在准备下载…', key: 'pdf-export' });
    setDownloadingPdf(true);
    try {
      await jobsApi.download(pdfJob.jobId, filename);
      message.success({ content: 'PDF 下载成功', key: 'pdf-export' });
      setPdfJob(null);
    } catch (err) {
      message.error({ content: formatError(err, '下载失败'), key: 'pdf-export' });
    } finally {
      setDownloadingPdf(false);
    }
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

  const columns: ColumnsType<Report> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 240,
      render: (name, record) => (
        // 批 10 demo-badge: rows seeded by scripts/seed_reports.py get
        // a "示例" Tag so operators can tell seed scaffolding apart
        // from reports they authored themselves. The Tag is non-interactive
        // — clicking it does nothing — but the tooltip explains the
        // origin so first-time users aren't confused about a row they
        // didn't create.
        <Space size={6} align="center">
          <Button
            type="link"
            style={{ padding: 0 }}
            onClick={() => navigate(`/reports/${record.id}`)}
          >
            {name}
          </Button>
          {record.is_demo && (
            <Tag color="blue" title="由 seed 脚本预置的示例报表 — 可正常编辑/删除">
              示例
            </Tag>
          )}
        </Space>
      ),
    },
    { title: '描述', dataIndex: 'description', key: 'description', width: 280, ellipsis: true },
    {
      title: '数据源',
      dataIndex: 'data_source_id',
      key: 'data_source',
      width: 150,
      render: (dsId) => {
        const ds = dataSources.find((d) => d.id === dsId);
        return ds ? ds.name : `ID: ${dsId}`;
      },
    },
    {
      title: '报表项',
      dataIndex: 'items',
      key: 'items',
      width: 80,
      render: (items) => items?.length || 0,
    },
    {
      title: '定时任务',
      key: 'schedule',
      width: 120,
      render: (_, record) =>
        record.is_scheduled ? (
          <Tag icon={<ClockCircleOutlined />} color="green">
            {record.cron_expression || '已配置'}
          </Tag>
        ) : (
          <Tag>未配置</Tag>
        ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active) => (active ? <Tag color="green">启用</Tag> : <Tag color="gray">禁用</Tag>),
    },
    {
      title: '操作',
      key: 'action',
      width: 320,
      render: (_, record) => {
        // If this row's report is the one currently in-flight, disable
        // the button and show a spinner — the user's already waiting on
        // it (the top card shows status).
        const inFlight =
          excelJob?.report.id === record.id &&
          (excelStatus.data?.status === 'pending' || excelStatus.data?.status === 'running');
        // Share button: only the owner or an admin can manage shares.
        // Backend enforces the same — we hide the affordance
        // client-side so non-owners don't see a broken button.
        const canShare =
          isAdmin || (currentUserId != null && record.owner_user_id === currentUserId);
        return (
          <Space size="small">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => navigate(`/reports/${record.id}`)}
            >
              编辑
            </Button>
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              onClick={() => navigate(`/reports/${record.id}/preview`)}
            >
              预览
            </Button>
            {canShare && (
              <Button
                type="link"
                size="small"
                icon={<ShareAltOutlined />}
                onClick={() => setShareTarget(record)}
              >
                分享
              </Button>
            )}
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'duplicate',
                    label: '复制',
                    icon: <CopyOutlined />,
                    disabled:
                      duplicateReport.isPending && duplicateReport.variables?.id === record.id,
                    onClick: () => handleDuplicate(record),
                  },
                  {
                    key: 'subscribe',
                    label: '订阅',
                    icon: <BellOutlined />,
                    onClick: () => setSubTarget(record),
                  },
                  { type: 'divider' },
                  {
                    key: 'excel',
                    label: inFlight ? 'Excel 导出中…' : '导出 Excel',
                    icon: <PlayCircleOutlined />,
                    disabled: inFlight,
                    onClick: () => handleGenerateExcel(record),
                  },
                  {
                    key: 'pdf',
                    label: '导出 PDF',
                    icon: <PlayCircleOutlined />,
                    disabled:
                      pdfJob?.report.id === record.id &&
                      (pdfStatus.data?.status === 'pending' ||
                        pdfStatus.data?.status === 'running'),
                    onClick: () => handleGeneratePdf(record),
                  },
                ],
              }}
              trigger={['click']}
            >
              <Button type="link" size="small" icon={<MoreOutlined />}>
                更多
              </Button>
            </Dropdown>
            <Popconfirm title="确定删除?" onConfirm={() => handleDelete(record.id)}>
              <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>报表管理</h2>
        <Space>
          <Button
            danger
            icon={<DeleteOutlined />}
            disabled={selectedRowKeys.length === 0}
            onClick={handleBatchDelete}
          >
            批量删除 {selectedRowKeys.length > 0 ? `(${selectedRowKeys.length})` : ''}
          </Button>
          <Button icon={<ClockCircleOutlined />} onClick={() => navigate('/scheduler')}>
            定时任务
          </Button>
          {/* 批 13 — route into the template marketplace. Sits next
              to "创建报表" so the two create affordances cluster. */}
          <Button
            icon={<AppstoreOutlined />}
            onClick={() => navigate('/reports/templates')}
          >
            从模板新建
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            创建报表
          </Button>
        </Space>
      </div>

      {excelJob && (
        <Card
          size="small"
          style={{ marginBottom: 16 }}
          title={`Excel 导出任务 — ${excelJob.report.name}`}
        >
          <Space size="middle" align="center">
            {(excelStatus.data?.status === 'pending' || excelStatus.data?.status === 'running') && (
              <Spin size="small" />
            )}
            {excelStatus.data?.status === 'pending' && <Tag color="blue">排队中</Tag>}
            {excelStatus.data?.status === 'running' && <Tag color="processing">执行中</Tag>}
            {excelStatus.data?.status === 'done' && <Tag color="success">已完成</Tag>}
            {excelStatus.data?.status === 'failed' && <Tag color="error">失败</Tag>}
            {!excelStatus.data && <Tag>初始化</Tag>}
            {excelStatus.data?.status === 'done' && (
              <Button
                type="primary"
                size="small"
                icon={<DownloadOutlined />}
                loading={downloadingExcel}
                onClick={handleDownloadExcel}
              >
                下载 Excel
              </Button>
            )}
            <Button size="small" icon={<CloseCircleOutlined />} onClick={() => setExcelJob(null)}>
              {excelStatus.data?.status === 'done' || excelStatus.data?.status === 'failed'
                ? '关闭'
                : '取消关注'}
            </Button>
          </Space>
          {excelStatus.data?.status === 'failed' && excelStatus.data?.error && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message="导出失败"
              description={excelStatus.data.error}
            />
          )}
        </Card>
      )}

      {pdfJob && (
        <Card
          size="small"
          style={{ marginBottom: 16 }}
          title={`PDF 导出任务 — ${pdfJob.report.name}`}
        >
          <Space size="middle" align="center">
            {(pdfStatus.data?.status === 'pending' || pdfStatus.data?.status === 'running') && (
              <Spin size="small" />
            )}
            {pdfStatus.data?.status === 'pending' && <Tag color="blue">排队中</Tag>}
            {pdfStatus.data?.status === 'running' && <Tag color="processing">执行中</Tag>}
            {pdfStatus.data?.status === 'done' && <Tag color="success">已完成</Tag>}
            {pdfStatus.data?.status === 'failed' && <Tag color="error">失败</Tag>}
            {!pdfStatus.data && <Tag>初始化</Tag>}
            {pdfStatus.data?.status === 'done' && (
              <Button
                type="primary"
                size="small"
                icon={<DownloadOutlined />}
                loading={downloadingPdf}
                onClick={handleDownloadPdf}
              >
                下载 PDF
              </Button>
            )}
            <Button size="small" icon={<CloseCircleOutlined />} onClick={() => setPdfJob(null)}>
              {pdfStatus.data?.status === 'done' || pdfStatus.data?.status === 'failed'
                ? '关闭'
                : '取消关注'}
            </Button>
          </Space>
          {pdfStatus.data?.status === 'failed' && pdfStatus.data?.error && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message="导出失败"
              description={pdfStatus.data.error}
            />
          )}
        </Card>
      )}

      <Table
        columns={columns}
        dataSource={reports}
        rowKey="id"
        loading={isPending}
        rowSelection={rowSelection}
        tableLayout="fixed"
        scroll={{ x: 1200 }}
        pagination={{
          ...pagination,
          total: reports.length,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        onChange={handleTableChange}
      />

      <Modal
        title="创建报表"
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        confirmLoading={createReport.isPending}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="报表名称"
            rules={[{ required: true, message: '请输入报表名称' }]}
          >
            <Input placeholder="例如: 月度销售报表" />
          </Form.Item>

          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="可选描述信息" />
          </Form.Item>

          <Form.Item
            name="data_source_id"
            label="数据源"
            rules={[{ required: true, message: '请选择数据源' }]}
          >
            <Select
              placeholder="请选择数据源"
              options={dataSources.map((ds) => ({
                value: ds.id,
                label: `${ds.name} (${ds.db_type})`,
              }))}
            />
          </Form.Item>

          <Form.Item name="output_formats" label="输出格式">
            <Select
              mode="multiple"
              placeholder="选择输出格式"
              options={[
                { value: 'excel', label: 'Excel' },
                { value: 'html', label: 'HTML' },
              ]}
            />
          </Form.Item>

          <Form.Item name="is_active" label="状态" valuePropName="checked">
            <Select
              options={[
                { value: true, label: '启用' },
                { value: false, label: '禁用' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <ReportShareModal
        visible={shareTarget != null}
        report={shareTarget}
        shares={shares.data}
        sharesLoading={shares.isPending}
        users={usersQuery.data}
        usersLoading={usersQuery.isPending}
        createPending={upsertShare.isPending}
        revokePending={revokeShare.isPending}
        onCreate={(payload) => {
          if (!shareTarget) return;
          upsertShare.mutate(
            { reportId: shareTarget.id, payload },
            {
              onSuccess: () => message.success('授权已添加'),
              onError: (err) => message.error(formatError(err, '授权失败')),
            },
          );
        }}
        onRevoke={(share) => {
          if (!shareTarget) return;
          revokeShare.mutate(
            { reportId: shareTarget.id, shareId: share.id },
            {
              onSuccess: () => message.success('已撤销'),
              onError: (err) => message.error(formatError(err, '撤销失败')),
            },
          );
        }}
        onCancel={() => setShareTarget(null)}
      />

      <SubscriptionModal
        open={subTarget != null}
        report={subTarget}
        onClose={() => setSubTarget(null)}
      />
    </div>
  );
}
