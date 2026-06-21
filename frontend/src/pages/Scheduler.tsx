import { useState, useEffect } from 'react';
import { Card, Table, Button, Space, Tag, Modal, Form, Input, Select, message, Popconfirm, Alert } from 'antd';
import { SyncOutlined, PlusOutlined, DeleteOutlined, ClockCircleOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { Report, SchedulerStatus, SchedulerJob } from '../types';
import { reportApi, schedulerApi } from '../api';
import { formatError } from '../utils/error';

type NotificationType = 'none' | 'webhook' | 'email';

function buildNotificationConfig(
  values: Record<string, unknown>
): Record<string, unknown> | null {
  const t = values.notification_type as NotificationType | undefined;
  if (t === 'webhook') {
    return { type: 'webhook', webhook_url: values.webhook_url ?? '' };
  }
  if (t === 'email') {
    return { type: 'email' };
  }
  return null;
}

export default function SchedulerPage() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const loadStatus = async () => {
    try {
      const data = await schedulerApi.getStatus();
      setStatus(data);
    } catch (err: unknown) {
      message.error(formatError(err, '加载调度器状态失败'));
    }
  };

  const loadReports = async () => {
    setLoading(true);
    try {
      const data = await reportApi.list({ is_active: true });
      setReports(data);
    } catch (err: unknown) {
      message.error(formatError(err, '加载报表失败'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
    loadReports();
     
  }, []);

  const handleSync = async () => {
    try {
      const result = await schedulerApi.sync();
      message.success(result.message);
      loadStatus();
    } catch (err: unknown) {
      message.error(formatError(err, '同步失败'));
    }
  };

  const handleAddSchedule = (report: Report) => {
    setSelectedReport(report);
    form.setFieldsValue({
      report_id: report.id,
      cron_expression: '0 9 * * * *',  // Default: 9:00 AM daily
      schedule_description: `定时生成 ${report.name}`,
      notification_type: 'none',
      webhook_url: '',
    });
    setModalVisible(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const notificationConfig = buildNotificationConfig(values);
      await schedulerApi.createJob(
        values.report_id,
        values.cron_expression,
        values.schedule_description,
        notificationConfig,
      );
      message.success('定时任务创建成功');
      setModalVisible(false);
      loadStatus();
      // Update report's is_scheduled flag
      setReports(prev => prev.map(r =>
        r.id === values.report_id ? { ...r, is_scheduled: true, cron_expression: values.cron_expression } : r
      ));
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteSchedule = async (reportId: number) => {
    try {
      await schedulerApi.deleteJob(reportId);
      message.success('定时任务已删除');
      loadStatus();
      // Update report's is_scheduled flag
      setReports(prev => prev.map(r =>
        r.id === reportId ? { ...r, is_scheduled: false } : r
      ));
    } catch (err: unknown) {
      message.error(formatError(err, '删除失败'));
    }
  };

  // Pause/resume a scheduled report by re-POSTing the same cron + notif config
  // with is_active flipped. Backend drops the APScheduler job on the next sync
  // when is_active=False (verified by test_create_job_with_is_active_false_
  // excluded_from_sync), and re-adds it when is_active=True. The cron and
  // notification_config are preserved on the Report row.
  const handleToggleActive = async (record: Report) => {
    const nextActive = !record.is_active;
    try {
      await schedulerApi.createJob(
        record.id,
        record.cron_expression ?? '',
        record.schedule_description,
        record.notification_config ?? null,
        nextActive,
      );
      message.success(nextActive ? '已启用' : '已暂停');
      setReports(prev => prev.map(r =>
        r.id === record.id ? { ...r, is_active: nextActive } : r
      ));
      loadStatus();
    } catch (err: unknown) {
      message.error(formatError(err, nextActive ? '启用失败' : '暂停失败'));
    }
  };

  const jobColumns: ColumnsType<SchedulerJob> = [
    { title: '任务ID', dataIndex: 'job_id', key: 'job_id' },
    { title: '下次执行', dataIndex: 'next_run', key: 'next_run', render: (v) => v || '-' },
    { title: '触发器', dataIndex: 'trigger', key: 'trigger' },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>定时任务管理</h2>
        <Space>
          <Button icon={<SyncOutlined />} onClick={handleSync}>
            同步调度器
          </Button>
          <Button icon={<ClockCircleOutlined />} onClick={loadStatus}>
            刷新状态
          </Button>
        </Space>
      </div>

      <Card title="调度器状态" style={{ marginBottom: 24 }}>
        {status ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Tag color={status.is_running ? 'green' : 'red'} style={{ width: 'fit-content' }}>
              {status.is_running ? '运行中' : '已停止'}
            </Tag>
            {status.jobs.length > 0 ? (
              <Table
                columns={jobColumns}
                dataSource={status.jobs}
                rowKey="job_id"
                size="small"
                pagination={false}
              />
            ) : (
              <Alert message="暂无运行的定时任务" type="info" showIcon />
            )}
          </Space>
        ) : (
          <div>加载中...</div>
        )}
      </Card>

      <Card title="报表定时任务配置">
        <Table
          columns={[
            { title: '报表名称', dataIndex: 'name', key: 'name' },
            { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
            { title: '定时任务', key: 'schedule', render: (_, record) => (
              record.is_scheduled ? (
                <Tag
                  icon={record.is_active ? <ClockCircleOutlined /> : <PauseCircleOutlined />}
                  color={record.is_active ? 'green' : 'orange'}
                >
                  {record.is_active ? record.cron_expression || '运行中' : '已暂停'}
                </Tag>
              ) : (
                <Tag>未配置</Tag>
              )
            )},
            { title: '操作', key: 'action', render: (_, record) => (
              record.is_scheduled ? (
                <Space>
                  <Button
                    type="link"
                    icon={record.is_active ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                    onClick={() => handleToggleActive(record)}
                  >
                    {record.is_active ? '暂停' : '启用'}
                  </Button>
                  <Popconfirm title="确定删除定时任务?" onConfirm={() => handleDeleteSchedule(record.id)}>
                    <Button type="link" danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ) : (
                <Button type="link" icon={<PlusOutlined />} onClick={() => handleAddSchedule(record)}>
                  添加定时任务
                </Button>
              )
            )},
          ]}
          dataSource={reports}
          rowKey="id"
          loading={loading}
          pagination={{ pageSize: 10 }}
        />
      </Card>

      <Modal
        title="添加定时任务"
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        confirmLoading={submitting}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="report_id" label="报表" rules={[{ required: true }]}>
            <Input disabled value={selectedReport?.name} />
          </Form.Item>

          <Form.Item
            name="cron_expression"
            label="Cron 表达式"
            rules={[{ required: true, message: '请输入 cron 表达式' }]}
            help="格式: 分 时 日 月 周 年 (例: 0 9 * * * * = 每天9点执行)"
          >
            <Input placeholder="0 9 * * * *" />
          </Form.Item>

          <Form.Item name="schedule_description" label="描述">
            <Input placeholder="定时任务描述" />
          </Form.Item>

          <Form.Item name="notification_type" label="通知方式">
            <Select
              options={[
                { value: 'none', label: '不通知' },
                { value: 'webhook', label: 'Webhook' },
                { value: 'email', label: 'Email (占位)' },
              ]}
            />
          </Form.Item>

          <Form.Item
            noStyle
            shouldUpdate={(prev, curr) =>
              prev.notification_type !== curr.notification_type
            }
          >
            {({ getFieldValue }) =>
              getFieldValue('notification_type') === 'webhook' ? (
                <Form.Item
                  name="webhook_url"
                  label="Webhook URL"
                  rules={[{
                    validator: (_, v) =>
                      !v || String(v).startsWith('http')
                        ? Promise.resolve()
                        : Promise.reject(new Error('URL 必须以 http 开头')),
                  }]}
                >
                  <Input placeholder="https://example.com/webhook" />
                </Form.Item>
              ) : null
            }
          </Form.Item>

          <Alert
            message="Cron 表达式说明"
            description={
              <div>
                <p>分(0-59) 时(0-23) 日(1-31) 月(1-12) 周(0-6) 年</p>
                <p>* = 任意值, - = 范围, / = 步长</p>
                <p>例: <code>0 9 * * * *</code> = 每天9:00</p>
                <p>例: <code>0 */2 * * * *</code> = 每2小时</p>
                <p>例: <code>0 0 * * 1 *</code> = 每周一0:00</p>
              </div>
            }
            type="info"
          />
        </Form>
      </Modal>
    </div>
  );
}
