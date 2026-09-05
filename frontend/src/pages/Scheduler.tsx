import { useState } from 'react';
import {
  Card,
  Table,
  Button,
  Space,
  Tag,
  Modal,
  Form,
  Input,
  Select,
  message,
  Popconfirm,
  Alert,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  ClockCircleOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { Report, SchedulerJob } from '../types';
import { formatError } from '../utils/error';
import {
  useCreateSchedulerJob,
  useDeleteSchedulerJob,
  useSchedulerStatus,
  useSyncScheduler,
} from '../queries/useScheduler';
import { useReports } from '../queries/useReports';

type NotificationType = 'none' | 'webhook' | 'email' | 'dingtalk' | 'feishu' | 'wechatwork';

function buildNotificationConfig(values: Record<string, unknown>): Record<string, unknown> | null {
  const t = values.notification_type as NotificationType | undefined;
  if (t === 'webhook' || t === 'feishu' || t === 'dingtalk') {
    return {
      type: t,
      webhook_url: values.webhook_url ?? '',
      secret: values.secret ?? '',
    };
  }
  if (t === 'wechatwork') {
    return { type: 'wechatwork', webhook_url: values.webhook_url ?? '' };
  }
  if (t === 'email') {
    return { type: 'email' };
  }
  return null;
}

export default function SchedulerPage() {
  const { data: status } = useSchedulerStatus();
  // `is_active: true` filter is part of the cache key; the Scheduler page
  // shows the active-reports table — the active filter matches the table.
  const { data: reports = [], isPending } = useReports({ is_active: true });
  const syncScheduler = useSyncScheduler();
  const createJob = useCreateSchedulerJob();
  const deleteJob = useDeleteSchedulerJob();

  const [modalVisible, setModalVisible] = useState(false);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [form] = Form.useForm();

  const handleSync = () => {
    syncScheduler.mutate(undefined, {
      onSuccess: (result) => message.success(result.message),
      onError: (err) => message.error(formatError(err, '同步失败')),
    });
  };

  const handleAddSchedule = (report: Report) => {
    setSelectedReport(report);
    form.setFieldsValue({
      report_id: report.id,
      cron_expression: '0 9 * * * *', // Default: 9:00 AM daily
      schedule_description: `定时生成 ${report.name}`,
      notification_type: 'none',
      webhook_url: '',
      secret: '',
    });
    setModalVisible(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      const notificationConfig = buildNotificationConfig(values);
      await createJob.mutateAsync({
        reportId: values.report_id,
        cronExpression: values.cron_expression,
        scheduleDescription: values.schedule_description,
        notificationConfig,
      });
      message.success('定时任务创建成功');
      setModalVisible(false);
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error.response?.data?.detail || '创建失败');
    }
  };

  const handleDeleteSchedule = (reportId: number) => {
    deleteJob.mutate(reportId, {
      onSuccess: () => message.success('定时任务已删除'),
      onError: (err) => message.error(formatError(err, '删除失败')),
    });
  };

  // Pause/resume a scheduled report by re-POSTing the same cron + notif
  // config with is_active flipped. Backend drops the APScheduler job on
  // the next sync when is_active=False, and re-adds it when is_active=True.
  // The cron and notification_config are preserved on the Report row.
  const handleToggleActive = (record: Report) => {
    const nextActive = !record.is_active;
    createJob.mutate(
      {
        reportId: record.id,
        cronExpression: record.cron_expression ?? '',
        scheduleDescription: record.schedule_description,
        notificationConfig: (record.notification_config as Record<string, unknown> | null) ?? null,
        isActive: nextActive,
      },
      {
        onSuccess: () => message.success(nextActive ? '已启用' : '已暂停'),
        onError: (err) => message.error(formatError(err, nextActive ? '启用失败' : '暂停失败')),
      },
    );
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
          <Button icon={<SyncOutlined />} onClick={handleSync} loading={syncScheduler.isPending}>
            同步调度器
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
            {
              title: '定时任务',
              key: 'schedule',
              render: (_, record) =>
                record.is_scheduled ? (
                  <Tag
                    icon={record.is_active ? <ClockCircleOutlined /> : <PauseCircleOutlined />}
                    color={record.is_active ? 'green' : 'orange'}
                  >
                    {record.is_active ? record.cron_expression || '运行中' : '已暂停'}
                  </Tag>
                ) : (
                  <Tag>未配置</Tag>
                ),
            },
            {
              title: '操作',
              key: 'action',
              render: (_, record) =>
                record.is_scheduled ? (
                  <Space>
                    <Button
                      type="link"
                      icon={record.is_active ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                      onClick={() => handleToggleActive(record)}
                    >
                      {record.is_active ? '暂停' : '启用'}
                    </Button>
                    <Popconfirm
                      title="确定删除定时任务?"
                      onConfirm={() => handleDeleteSchedule(record.id)}
                    >
                      <Button type="link" danger icon={<DeleteOutlined />}>
                        删除
                      </Button>
                    </Popconfirm>
                  </Space>
                ) : (
                  <Button
                    type="link"
                    icon={<PlusOutlined />}
                    onClick={() => handleAddSchedule(record)}
                  >
                    添加定时任务
                  </Button>
                ),
            },
          ]}
          dataSource={reports}
          rowKey="id"
          loading={isPending}
          pagination={{ pageSize: 10 }}
        />
      </Card>

      <Modal
        title="添加定时任务"
        open={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        confirmLoading={createJob.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="report_id" label="报表" rules={[{ required: true }]}>
            <Input disabled value={selectedReport?.name} />
          </Form.Item>

          <Form.Item
            name="cron_expression"
            label="Cron 表达式"
            rules={[
              { required: true, message: '请输入 cron 表达式' },
              {
                validator: (_, value) => {
                  if (!value) return Promise.resolve();
                  const parts = String(value).trim().split(/\s+/);
                  if (parts.length !== 6) {
                    return Promise.reject(
                      new Error(
                        'Cron 表达式需要6个字段（分 时 日 月 周 年），当前仅有 ' +
                          parts.length +
                          ' 个',
                      ),
                    );
                  }
                  return Promise.resolve();
                },
              },
            ]}
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
                { value: 'feishu', label: '飞书' },
                { value: 'dingtalk', label: '钉钉' },
                { value: 'wechatwork', label: '企业微信' },
              ]}
            />
          </Form.Item>

          <Form.Item
            noStyle
            shouldUpdate={(prev, curr) => prev.notification_type !== curr.notification_type}
          >
            {({ getFieldValue }) => {
              const t = getFieldValue('notification_type') as NotificationType;
              if (
                t === 'webhook' ||
                t === 'feishu' ||
                t === 'dingtalk' ||
                t === 'wechatwork'
              ) {
                return (
                  <>
                    <Form.Item
                      name="webhook_url"
                      label={
                        t === 'feishu'
                          ? '飞书 Webhook URL'
                          : t === 'dingtalk'
                            ? '钉钉 Webhook URL'
                            : t === 'wechatwork'
                              ? '企业微信 Webhook URL'
                              : 'Webhook URL'
                      }
                      rules={[
                        {
                          validator: (_, v) =>
                            !v || String(v).startsWith('http')
                              ? Promise.resolve()
                              : Promise.reject(new Error('URL 必须以 http 开头')),
                        },
                      ]}
                    >
                      <Input
                        placeholder={
                          t === 'feishu'
                            ? 'https://open.feishu.cn/open-apis/bot/v2/hook/...'
                            : t === 'dingtalk'
                              ? 'https://oapi.dingtalk.com/robot/send?access_token=...'
                              : t === 'wechatwork'
                                ? 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...'
                                : 'https://example.com/webhook'
                        }
                      />
                    </Form.Item>
                    {(t === 'webhook' || t === 'feishu' || t === 'dingtalk') && (
                      <Form.Item
                        name="secret"
                        label={
                          t === 'feishu'
                            ? '飞书签名密钥 (可选)'
                            : t === 'dingtalk'
                              ? '钉钉加签密钥 (可选)'
                              : 'Webhook 签名密钥 (可选)'
                        }
                        tooltip={
                          t === 'feishu'
                            ? '开启签名校验后，飞书会在 JSON body 里追加 timestamp + sign 字段'
                            : t === 'dingtalk'
                              ? '钉钉机器人「安全设置 → 加签」里的密钥；开启加签后请求 URL 必须带 timestamp + sign 参数，否则机器人返回 40035'
                              : '设置后，webhook 请求会带上 X-Webhook-Timestamp 与 X-Webhook-Signature 头；不填则沿用后端 WEBHOOK_SECRET 全局配置'
                        }
                      >
                        <Input.Password
                          placeholder={
                            t === 'feishu'
                              ? 'SEC...'
                              : t === 'dingtalk'
                                ? 'SEC...'
                                : 'shared-secret'
                          }
                        />
                      </Form.Item>
                    )}
                  </>
                );
              }
              return null;
            }}
          </Form.Item>

          <Alert
            message="Cron 表达式说明"
            description={
              <div>
                <p>分(0-59) 时(0-23) 日(1-31) 月(1-12) 周(0-6) 年</p>
                <p>* = 任意值, - = 范围, / = 步长</p>
                <p>
                  例: <code>0 9 * * * *</code> = 每天9:00
                </p>
                <p>
                  例: <code>0 */2 * * * *</code> = 每2小时
                </p>
                <p>
                  例: <code>0 0 * * 1 *</code> = 每周一0:00
                </p>
              </div>
            }
            type="info"
          />
        </Form>
      </Modal>
    </div>
  );
}
