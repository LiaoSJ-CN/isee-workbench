/** Create-subscription modal for a Dashboard (批 14.3).
 *
 * Mirrors :component:`SubscriptionModal` 1:1 — same notification union,
 * same cron helper, same per-type conditional fields. The only delta is
 * the target: instead of ``report_id`` we bind to ``dashboard_id``, and
 * the underlying client is :func:`dashboardSubscriptionApi.create`.
 *
 * Implementation note: this file is intentionally close to a copy rather
 * than a shared component because (a) the schemas diverge in 14.4 when
 * we wire real dispatch, and (b) the report modal will gain report-only
 * fields (e.g. "include raw Excel") that don't apply to dashboards.
 */

import { useEffect, useMemo } from 'react';
import { Form, Input, Modal, Select, Space, Typography, message } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { dashboardSubscriptionApi } from '../api';
import type {
  Dashboard,
  DashboardSubscription,
  NotificationConfig,
  NotificationType,
} from '../types';

const { Text } = Typography;

interface SubscriptionFormValues {
  cron_expression: string;
  notification_type?: NotificationType;
  webhook_url?: string;
  feishu_url?: string;
  wechatwork_url?: string;
  feishu_secret?: string;
  webhook_secret?: string;
  email_to?: string;
  email_subject?: string;
}

interface DashboardSubscriptionModalProps {
  open: boolean;
  dashboard: Dashboard | null;
  onClose: () => void;
}

function buildNotificationConfig(values: SubscriptionFormValues): NotificationConfig | null {
  const t = values.notification_type;
  if (!t || t === 'none') return null;
  switch (t) {
    case 'email':
      return {
        type: 'email',
        to: (values.email_to ?? '')
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        subject: values.email_subject ?? '看板已生成',
      };
    case 'webhook':
      return {
        type: 'webhook',
        url: values.webhook_url ?? '',
        secret: values.webhook_secret || null,
      };
    case 'feishu':
      return {
        type: 'feishu',
        webhook_url: values.feishu_url ?? '',
        secret: values.feishu_secret || null,
      };
    case 'dingtalk':
      return {
        type: 'dingtalk',
        webhook_url: values.webhook_url ?? '',
        secret: values.webhook_secret || null,
      };
    case 'wechatwork':
      return {
        type: 'wechatwork',
        webhook_url: values.wechatwork_url ?? '',
      };
    default:
      return null;
  }
}

export function DashboardSubscriptionModal({
  open,
  dashboard,
  onClose,
}: DashboardSubscriptionModalProps) {
  const [form] = Form.useForm<SubscriptionFormValues>();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({
        notification_type: 'email',
        cron_expression: '0 9 * * * 2026',
        email_subject: `${dashboard?.name ?? '看板'} 已生成`,
      });
    }
  }, [open, dashboard, form]);

  const createMut = useMutation({
    mutationFn: async (values: SubscriptionFormValues): Promise<DashboardSubscription> => {
      if (!dashboard) throw new Error('No dashboard selected');
      return dashboardSubscriptionApi.create({
        dashboard_id: dashboard.id,
        cron_expression: values.cron_expression,
        parameters: {},
        notification_config: buildNotificationConfig(values),
      });
    },
    onSuccess: () => {
      message.success('看板订阅已创建');
      queryClient.invalidateQueries({ queryKey: ['my-dashboard-subscriptions'] });
      onClose();
    },
    onError: (err: Error) => {
      message.error(`看板订阅创建失败: ${err.message}`);
    },
  });

  const renderNotificationFields = useMemo(() => {
    return (
      <Form.Item
        noStyle
        shouldUpdate={(prev, curr) => prev.notification_type !== curr.notification_type}
      >
        {({ getFieldValue }) => {
          const t: NotificationType | undefined = getFieldValue('notification_type');
          switch (t) {
            case 'email':
              return (
                <>
                  <Form.Item
                    name="email_to"
                    label="收件人"
                    rules={[{ required: true, message: '请输入收件人邮箱' }]}
                  >
                    <Input placeholder="ops@example.com, finance@example.com" />
                  </Form.Item>
                  <Form.Item
                    name="email_subject"
                    label="邮件主题"
                    rules={[{ required: true, message: '请输入邮件主题' }]}
                  >
                    <Input placeholder="看板已生成" />
                  </Form.Item>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    SMTP 服务器需在 backend/.env 中配置 SMTP_HOST / SMTP_PORT / SMTP_USER /
                    SMTP_PASSWORD。
                  </Text>
                </>
              );
            case 'webhook':
            case 'dingtalk':
              return (
                <>
                  <Form.Item
                    name="webhook_url"
                    label="Webhook URL"
                    rules={[
                      { required: true, message: '请输入 webhook URL' },
                      { type: 'url', message: '请输入合法的 URL' },
                    ]}
                  >
                    <Input placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." />
                  </Form.Item>
                  <Form.Item name="webhook_secret" label="签名密钥 (可选)">
                    <Input.Password placeholder="SEC... (留空则使用后端全局密钥)" />
                  </Form.Item>
                </>
              );
            case 'feishu':
              return (
                <>
                  <Form.Item
                    name="feishu_url"
                    label="飞书 Webhook URL"
                    rules={[
                      { required: true, message: '请输入飞书 webhook URL' },
                      { type: 'url', message: '请输入合法的 URL' },
                    ]}
                  >
                    <Input placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
                  </Form.Item>
                  <Form.Item name="feishu_secret" label="签名密钥 (可选)">
                    <Input.Password placeholder="SEC..." />
                  </Form.Item>
                </>
              );
            case 'wechatwork':
              return (
                <Form.Item
                  name="wechatwork_url"
                  label="企业微信 Webhook URL"
                  rules={[
                    { required: true, message: '请输入企业微信 webhook URL' },
                    { type: 'url', message: '请输入合法的 URL' },
                  ]}
                >
                  <Input placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." />
                </Form.Item>
              );
            default:
              return (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  当前订阅不会发送通知 — 看板仍会按 cron 触发并保存快照。
                </Text>
              );
          }
        }}
      </Form.Item>
    );
  }, []);

  return (
    <Modal
      open={open}
      title={
        <Space>
          <span>订阅看板</span>
          {dashboard ? (
            <Text type="secondary" style={{ fontSize: 13 }}>
              {dashboard.name}
            </Text>
          ) : null}
        </Space>
      }
      okText="创建"
      cancelText="取消"
      confirmLoading={createMut.isPending}
      onCancel={onClose}
      onOk={() => {
        form
          .validateFields()
          .then((values) => createMut.mutate(values))
          .catch(() => {
            // antd shows inline errors; we just keep the modal open.
          });
      }}
      destroyOnClose
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          cron_expression: '0 9 * * * 2026',
          notification_type: 'email',
        }}
      >
        <Form.Item
          name="cron_expression"
          label="cron 表达式"
          extra="6 字段: 分 时 日 月 周 年。例如 0 9 * * * 2026 表示每天 9 点。"
          rules={[
            { required: true, message: '请输入 cron 表达式' },
            {
              validator: (_, value: string) => {
                if (!value) return Promise.resolve();
                const parts = value.trim().split(/\s+/);
                if (parts.length !== 6) {
                  return Promise.reject(new Error('cron 必须包含 6 个字段'));
                }
                return Promise.resolve();
              },
            },
          ]}
        >
          <Input placeholder="0 9 * * * 2026" />
        </Form.Item>

        <Form.Item
          name="notification_type"
          label="通知渠道"
          rules={[{ required: true, message: '请选择通知渠道' }]}
        >
          <Select
            options={[
              { value: 'none', label: '不发送通知' },
              { value: 'email', label: '邮件 (SMTP)' },
              { value: 'webhook', label: '通用 Webhook' },
              { value: 'dingtalk', label: '钉钉' },
              { value: 'feishu', label: '飞书' },
              { value: 'wechatwork', label: '企业微信' },
            ]}
          />
        </Form.Item>

        {renderNotificationFields}
      </Form>
    </Modal>
  );
}
