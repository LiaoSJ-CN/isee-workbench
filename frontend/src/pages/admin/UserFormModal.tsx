/**
 * Create / edit user modal (批 user-management S3+S4).
 *
 * Two modes:
 *
 *   - **create**: ``username`` + ``password`` + ``role`` required. The
 *     backend rejects duplicate usernames with 409, which the form
 *     surfaces inline via the mutation's onError.
 *   - **edit**: ``role`` + ``disabled`` patch. ``username`` is
 *     immutable (deliberately — changing it would break the audit FK
 *     readability). When the operator edits their own row the ``role``
 *     field is disabled — the backend enforces the same last-admin
 *     guard via 403, but disabling the field gives instant feedback.
 *
 * The form pattern mirrors ``SubscriptionModal.tsx:100`` — own the
 * ``Form.useForm()`` instance, reset on open, run the mutation from
 * ``onOk`` after ``form.validateFields()`` resolves.
 */

import { useEffect } from 'react';
import {
  Alert,
  Form,
  Input,
  Modal,
  Select,
  Switch,
  message,
} from 'antd';

import { formatError } from '../../utils/error';
import type { AdminUserRole, UserCreate, UserResponse, UserUpdate } from '../../types';

interface UserFormModalProps {
  open: boolean;
  mode: 'create' | 'edit';
  user?: UserResponse | null;
  /** Current logged-in admin id — used to disable the role field when
   *  the operator edits their own row (prevents accidental self-demote
   *  on the frontend; the backend still enforces via 403). */
  currentUserId: number | null | undefined;
  pending: boolean;
  onSubmit: (
    payload: UserCreate | UserUpdate,
  ) => Promise<UserResponse>;
  onClose: () => void;
}

interface FormValues {
  username: string;
  password: string;
  role: AdminUserRole;
  disabled: boolean;
}

const ROLE_OPTIONS: { value: AdminUserRole; label: string }[] = [
  { value: 'admin', label: '管理员 (admin)' },
  { value: 'editor', label: '编辑 (editor)' },
  { value: 'viewer', label: '查看者 (viewer)' },
];

export function UserFormModal({
  open,
  mode,
  user,
  currentUserId,
  pending,
  onSubmit,
  onClose,
}: UserFormModalProps) {
  const [form] = Form.useForm<FormValues>();
  const isSelf = mode === 'edit' && user?.id === currentUserId;

  // Reset on every open / target switch — same pattern as
  // SubscriptionModal.tsx:106.
  useEffect(() => {
    if (open) {
      form.resetFields();
      if (mode === 'edit' && user) {
        form.setFieldsValue({
          username: user.username,
          role: user.role,
          disabled: user.disabled,
        });
      } else {
        form.setFieldsValue({
          role: 'viewer',
          disabled: false,
        });
      }
    }
  }, [open, mode, user, form]);

  const handleOk = () => {
    form
      .validateFields()
      .then(async (values) => {
        try {
          if (mode === 'create') {
            await onSubmit({
              username: values.username,
              password: values.password,
              role: values.role,
            });
            message.success(`用户「${values.username}」已创建`);
          } else {
            await onSubmit({
              role: values.role,
              disabled: values.disabled,
            });
            message.success(`用户「${user?.username ?? ''}」已更新`);
          }
          onClose();
        } catch (err) {
          // 409 (duplicate username) + 403 (self-protect) + 422 (short
          // password) all surface here — the helper unwraps the
          // axios detail envelope.
          message.error(formatError(err, `${mode === 'create' ? '创建' : '更新'}用户失败`));
        }
      })
      .catch(() => {
        // antd already surfaces inline validation errors; we just
        // stop the OK handler from closing the modal.
      });
  };

  return (
    <Modal
      title={mode === 'create' ? '新建用户' : `编辑用户 — ${user?.username ?? ''}`}
      open={open}
      onCancel={onClose}
      onOk={handleOk}
      okText={mode === 'create' ? '创建' : '保存'}
      cancelText="取消"
      confirmLoading={pending}
      destroyOnClose
      width={520}
    >
      <Form form={form} layout="vertical" preserve={false}>
        {isSelf && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="您正在编辑自己的账号 — 角色选择已禁用, 防止误把自己从管理员降级。后端仍会拦截 403 self-protection。"
          />
        )}

        <Form.Item
          name="username"
          label="用户名"
          rules={[
            { required: true, message: '请输入用户名' },
            { min: 1, max: 255, message: '用户名长度需在 1-255 个字符之间' },
          ]}
        >
          <Input
            placeholder="例如：alice"
            autoComplete="off"
            disabled={mode === 'edit'}
          />
        </Form.Item>

        {mode === 'create' && (
          <Form.Item
            name="password"
            label="初始密码"
            rules={[
              { required: true, message: '请输入初始密码' },
              { min: 8, message: '密码长度至少 8 个字符' },
              { max: 255, message: '密码长度不能超过 255 个字符' },
            ]}
          >
            <Input.Password
              placeholder="至少 8 个字符"
              autoComplete="new-password"
            />
          </Form.Item>
        )}

        <Form.Item
          name="role"
          label="角色"
          rules={[{ required: true, message: '请选择角色' }]}
        >
          <Select
            options={ROLE_OPTIONS}
            disabled={isSelf}
            data-testid="role-select"
          />
        </Form.Item>

        {mode === 'edit' && (
          <Form.Item
            name="disabled"
            label="账号状态"
            valuePropName="checked"
            extra="禁用后该用户将无法登录, 但审计日志中已有的 actor_user_id 仍可追溯到原 username。"
          >
            <Switch
              checkedChildren="禁用"
              unCheckedChildren="启用"
              data-testid="disabled-switch"
            />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}