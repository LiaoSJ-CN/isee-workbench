/** Create-subscription modal (批 8.3).
 *
 * Opened from the "订阅" button on the report-list row. The
 * subscription binds the current user to a cron + (optional)
 * notification destination. Owner-scoped — there's no UI for
 * subscribing on behalf of someone else, intentionally; the
 * /subscriptions route is hard-locked to ``request.user.id``.
 *
 * Form shape mirrors :class:`ReportSubscriptionCreate` on the
 * backend. ``report_id`` is pre-filled from the row that opened
 * the modal and never editable here — the operator picks the
 * report by which row they clicked "订阅" on.
 */

import { useEffect, useMemo } from 'react';
import {
  Form,
  Input,
  Modal,
  Select,
  Space,
  Typography,
  message,
} from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { subscriptionApi } from '../api';
import type {
  NotificationConfig,
  NotificationType,
  Report,
  ReportSubscription,
} from '../types';

const { Text } = Typography;

// ---- types ----

interface SubscriptionFormValues {
  cron_expression: string;
  notification_type?: NotificationType;
  // Per-type URL fields. Only the one matching ``notification_type``
  // is sent; the others are ignored. We keep them on the form so
  // switching between providers doesn't lose what the user typed.
  webhook_url?: string;
  feishu_url?: string;
  wechatwork_url?: string;
  feishu_secret?: string;
  webhook_secret?: string;
  email_to?: string; // comma-separated
  email_subject?: string;
}

// ---- props ----

interface SubscriptionModalProps {
  open: boolean;
  report: Report | null;
  onClose: () => void;
}

// ---- helpers ----

/** Build a NotificationConfig from the raw form values. Returns
 *  ``null`` when the user picked "no notification" — the backend
 *  stores ``notification_config=NULL`` in that case and the
 *  worker still produces the file (just doesn't deliver it). */
function buildNotificationConfig(
  values: SubscriptionFormValues,
): NotificationConfig | null {
  const t = values.notification_type;
  if (!t || t === 'none') return null;
  switch (t) {
    case 'email':
      return {
        type: 'email',
        // Split comma-separated input into the list the backend
        // expects. Trim each address and drop empties so a
        // trailing comma doesn't become a 422.
        to: (values.email_to ?? '')
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        subject: values.email_subject ?? '报表已生成',
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

// ---- component ----

export function SubscriptionModal({
  open,
  report,
  onClose,
}: SubscriptionModalProps) {
  const [form] = Form.useForm<SubscriptionFormValues>();
  const queryClient = useQueryClient();

  // Reset the form whenever the modal opens with a new target so
  // we never inherit stale state from a previous "订阅" click.
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({
        notification_type: 'email',
        cron_expression: '0 9 * * * 2026', // every day 9am, year pinned
        email_subject: `${report?.name ?? '报表'} 已生成`,
      });
    }
  }, [open, report, form]);

  const createMut = useMutation({
    mutationFn: async (
      values: SubscriptionFormValues,
    ): Promise<ReportSubscription> => {
      if (!report) throw new Error('No report selected');
      return subscriptionApi.create({
        report_id: report.id,
        cron_expression: values.cron_expression,
        parameters: {},
        notification_config: buildNotificationConfig(values),
      });
    },
    onSuccess: () => {
      message.success('订阅已创建');
      queryClient.invalidateQueries({ queryKey: ['my-subscriptions'] });
      onClose();
    },
    onError: (err: Error) => {
      // The backend's ``InvalidCronExpression`` surfaces as a 400
      // with the cron diagnostic in the detail; show it verbatim
      // so the operator can fix the field without guessing.
      message.error(`订阅创建失败: ${err.message}`);
    },
  });

  // Show different per-type fields. ``shouldUpdate`` re-renders
  // only when ``notification_type`` changes so we don't re-render
  // the whole form on every keystroke. Using a fragment to wrap
  // the URL field + the optional secret keeps both branches
  // reachable (a single if/return would lose one branch — same
  // bug we hit on the Scheduler page in batch 8.4).
  const renderNotificationFields = useMemo(() => {
    return (
      <Form.Item
        noStyle
        shouldUpdate={(prev, curr) =>
          prev.notification_type !== curr.notification_type
        }
      >
        {({ getFieldValue }) => {
          const t: NotificationType | undefined = getFieldValue(
            'notification_type',
          );
          switch (t) {
            case 'email':
              return (
                <>
                  <Form.Item
                    name="email_to"
                    label="收件人"
                    rules={[
                      { required: true, message: '请输入收件人邮箱' },
                    ]}
                  >
                    <Input placeholder="ops@example.com, finance@example.com" />
                  </Form.Item>
                  <Form.Item
                    name="email_subject"
                    label="邮件主题"
                    rules={[
                      { required: true, message: '请输入邮件主题' },
                    ]}
                  >
                    <Input placeholder="报表已生成" />
                  </Form.Item>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    SMTP 服务器需在 backend/.env 中配置 SMTP_HOST / SMTP_PORT /
                    SMTP_USER / SMTP_PASSWORD，未配置时邮件会记录错误但订阅本身仍生效。
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
                  <Form.Item
                    name="webhook_secret"
                    label="签名密钥 (可选)"
                  >
                    <Input.Password
                      placeholder="SEC... (留空则使用后端全局密钥)"
                    />
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
                  <Form.Item
                    name="feishu_secret"
                    label="签名密钥 (可选)"
                  >
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
              // 'none' or unset — show a hint instead of extra
              // fields so the form still has a clear empty state.
              return (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  当前订阅不会发送通知 — 报表仍会按 cron 触发并保存到文件。
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
          <span>订阅报表</span>
          {report ? (
            <Text type="secondary" style={{ fontSize: 13 }}>
              {report.name}
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
            // antd already surfaces inline validation errors; we
            // just stop the OK handler from closing the modal.
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
                  return Promise.reject(
                    new Error('cron 必须包含 6 个字段'),
                  );
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