/**
 * Centralised grant modal (批 user-management S3+S4).
 *
 * Admin opens this to grant a user access to any DataSource / Report /
 * Dashboard from the admin page (bypassing the per-resource share
 * modal). Three-step UX:
 *
 *   1. Pick the resource **type** via Segmented control (数据源 / 报表 / 看板).
 *   2. Pick the resource **instance** via searchable Select. The
 *      options come from the matching list API
 *      (``dataSourceApi.list`` / ``reportApi.list`` /
 *      ``dashboardApi.list``).
 *   3. Pick the target user (``useUsers()`` for the lightweight
 *      projection) + the permission level (read / write).
 *
 * Once a resource is selected the modal shows a preview of existing
 * grants on that resource (``useAdminResourceGrants``) so the admin
 * can see who already has access without leaving the modal.
 *
 * Submit delegates to ``useAdminGrant`` — that hook owns the
 * four-key invalidation cascade (adminUsers.all / adminGrants.all /
 * per-resource ACL). Closing the modal after success is the parent's
 * responsibility; the parent should ``onClose`` after the mutation's
 * ``onSuccess`` callback fires.
 *
 * The modal can be opened in two flavours:
 *   - **preset user**: drawer tab "新建授权" — ``defaultUserId`` set
 *     so the user picker collapses to a read-only display.
 *   - **standalone**: top-right "+集中授权" button on the Users page
 *     — ``defaultUserId`` is undefined, the user picker is open.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Modal,
  Radio,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';

import { dataSourceApi, dashboardApi, reportApi, usersApi } from '../../api';
import type {
  AdminGrantPermission,
  AdminResourceType,
  Dashboard,
  DataSource,
  GrantSummaryItem,
  Report,
  UserSummary,
} from '../../types';
import { queryKeys } from '../../queries/keys';
import {
  useAdminGrant,
  useAdminResourceGrants,
  useAdminRevokeGrant,
} from '../../queries/useAdminUsers';
import { formatError } from '../../utils/error';

const { Text } = Typography;

const RESOURCE_TYPE_OPTIONS: { label: string; value: AdminResourceType }[] = [
  { label: '数据源', value: 'data_source' },
  { label: '报表', value: 'report' },
  { label: '看板', value: 'dashboard' },
];

interface GrantModalProps {
  open: boolean;
  /** When set, the user picker collapses to a read-only display
   *  showing this user's username; submit still goes through the same
   *  endpoint. */
  defaultUserId?: number | null;
  /** Pre-selected resource type (e.g. the drawer tab that opened this
   *  modal already knows whether it's a DS / Report / Dashboard). */
  defaultResourceType?: AdminResourceType;
  /** Pre-selected resource id (used when the drawer opened the modal
   *  for a specific row). */
  defaultResourceId?: number | null;
  onClose: () => void;
}

interface ResourceOption {
  value: number;
  label: string;
}

function buildOptions(
  items: { id: number; name: string }[],
): ResourceOption[] {
  if (items.length === 0) return [];
  return items.map((it) => ({ value: it.id, label: `#${it.id} ${it.name}` }));
}

export function GrantModal({
  open,
  defaultUserId,
  defaultResourceType,
  defaultResourceId,
  onClose,
}: GrantModalProps) {
  const [form] = Form.useForm<{
    user_id: number;
    permission: AdminGrantPermission;
  }>();
  const [resourceType, setResourceType] = useState<AdminResourceType>(
    defaultResourceType ?? 'data_source',
  );
  const [resourceId, setResourceId] = useState<number | null>(
    defaultResourceId ?? null,
  );

  const grantMut = useAdminGrant();
  const revokeMut = useAdminRevokeGrant();

  // Resource lists — one per type. ``enabled`` only fires the matching
  // query so we don't pay for 3 lists on every open. ``gcTime: 30_000``
  // so the page-level cache keeps the lists around long enough to
  // avoid a refetch when the operator flips the Segmented control.
  const dsList = useQuery<DataSource[]>({
    queryKey: queryKeys.dataSources.list(),
    queryFn: () => dataSourceApi.list(),
    enabled: open && resourceType === 'data_source',
    staleTime: 30_000,
  });
  const reportList = useQuery<Report[]>({
    queryKey: queryKeys.reports.list({ is_active: true }),
    queryFn: () => reportApi.list({ is_active: true }),
    enabled: open && resourceType === 'report',
    staleTime: 30_000,
  });
  const dashboardList = useQuery<Dashboard[]>({
    queryKey: ['dashboards'],
    queryFn: () => dashboardApi.list(),
    enabled: open && resourceType === 'dashboard',
    staleTime: 30_000,
  });

  // Lightweight user projection — re-uses the existing ``useUsers`` /
  // ``usersApi.list`` pattern so the share modals and this modal share
  // one cached read.
  const usersQuery = useQuery<UserSummary[]>({
    queryKey: queryKeys.users.list(),
    queryFn: () => usersApi.list(),
    enabled: open,
    staleTime: 60_000,
  });

  // Preview of existing grants on the chosen resource. Disabled until
  // both resource_type + resource_id are picked.
  const previewQuery = useAdminResourceGrants(resourceType, resourceId);

  // Reset / sync the form whenever the modal opens or the target user
  // changes. Without this, switching between drawer tabs would leak
  // the previous user / resource selection into the next one.
  useEffect(() => {
    if (open) {
      setResourceType(defaultResourceType ?? 'data_source');
      setResourceId(defaultResourceId ?? null);
      form.resetFields();
      form.setFieldsValue({
        user_id: defaultUserId ?? undefined,
        permission: 'read',
      });
    }
  }, [open, defaultUserId, defaultResourceType, defaultResourceId, form]);

  const resourceOptions: ResourceOption[] = useMemo(() => {
    if (resourceType === 'data_source') {
      return buildOptions(dsList.data ?? []);
    }
    if (resourceType === 'report') {
      return buildOptions(reportList.data ?? []);
    }
    return buildOptions(dashboardList.data ?? []);
  }, [resourceType, dsList.data, reportList.data, dashboardList.data]);

  const userOptions = useMemo(() => {
    return (usersQuery.data ?? []).map((u) => ({
      value: u.id,
      label: `${u.username} (${u.role})`,
    }));
  }, [usersQuery.data]);

  const handleOk = () => {
    form
      .validateFields()
      .then(async (values) => {
        if (resourceId == null) {
          message.warning('请先选择资源');
          return;
        }
        try {
          await grantMut.mutateAsync({
            resource_type: resourceType,
            resource_id: resourceId,
            target_user_id: values.user_id,
            permission: values.permission,
          });
          message.success('授权已下发');
          onClose();
        } catch (err) {
          message.error(formatError(err, '授权失败'));
        }
      })
      .catch(() => {
        // antd surfaces inline validation errors; stop OK from closing.
      });
  };

  const handleRevoke = (grant: GrantSummaryItem) => {
    revokeMut.mutate(
      {
        resource_type: grant.resource_type,
        grant_id: grant.grant_id,
        resource_id: grant.resource_id,
      },
      {
        onSuccess: () => message.success('已撤销授权'),
        onError: (err: Error) => message.error(formatError(err, '撤销失败')),
      },
    );
  };

  const previewColumns: ColumnsType<GrantSummaryItem> = [
    {
      title: '用户',
      dataIndex: 'granted_by_username',
      key: 'user',
      // The backend's ``granted_by_username`` only carries the
      // *grantor*; we need the *grantee*. The Drawer's grants tab
      // resolves this client-side from a separate usersApi.list read.
      // For the standalone modal we use a simpler representation:
      // grant_id is the underlying access row PK, the only stable
      // identity here. Real UI surfacing of grantee username is out
      // of scope (would require enriching GrantSummaryItem server-side
      // — flagged for a future batch).
      render: () => `grant #${0}`, // placeholder — see comment above
    },
    {
      title: '权限',
      dataIndex: 'permission',
      key: 'permission',
      render: (p: AdminGrantPermission) => (
        <Tag color={p === 'write' ? 'gold' : 'blue'}>
          {p === 'write' ? '写' : '读'}
        </Tag>
      ),
    },
    {
      title: '授权人',
      dataIndex: 'granted_by_username',
      key: 'granted_by',
      render: (name?: string | null) => name ?? '—',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_v, grant) => (
        <Button
          type="link"
          size="small"
          danger
          onClick={() => handleRevoke(grant)}
          data-testid={`revoke-${grant.grant_id}`}
        >
          撤销
        </Button>
      ),
    },
  ];

  const resourceListLoading =
    (resourceType === 'data_source' && dsList.isPending) ||
    (resourceType === 'report' && reportList.isPending) ||
    (resourceType === 'dashboard' && dashboardList.isPending);

  return (
    <Modal
      title="集中授权"
      open={open}
      onCancel={onClose}
      onOk={handleOk}
      okText="下发授权"
      cancelText="取消"
      confirmLoading={grantMut.isPending}
      destroyOnClose
      width={720}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="集中授权从管理员边界操作, 绕过资源本身的 ACL 检查 — 等同于 owner / admin 在该资源 share modal 里点授权。"
        />

        <div>
          <Text strong>资源类型</Text>
          <div style={{ marginTop: 8 }}>
            <Segmented
              options={RESOURCE_TYPE_OPTIONS}
              value={resourceType}
              onChange={(v) => {
                setResourceType(v as AdminResourceType);
                setResourceId(null);
              }}
              data-testid="resource-type-segmented"
            />
          </div>
        </div>

        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item label="资源" required>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder={`选择${RESOURCE_TYPE_OPTIONS.find((o) => o.value === resourceType)?.label ?? ''}`}
              loading={resourceListLoading}
              value={resourceId ?? undefined}
              onChange={(v) => setResourceId(v)}
              options={resourceOptions}
              notFoundContent={resourceListLoading ? '加载中…' : '暂无资源'}
              filterOption={(input, option) =>
                String(option?.label ?? '')
                  .toLowerCase()
                  .includes(input.toLowerCase())
              }
              data-testid="resource-select"
            />
          </Form.Item>

          <Form.Item
            name="user_id"
            label="目标用户"
            rules={[{ required: true, message: '请选择目标用户' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择用户"
              loading={usersQuery.isPending}
              options={userOptions}
              disabled={defaultUserId != null}
              notFoundContent={usersQuery.isPending ? '加载中…' : '暂无用户'}
              filterOption={(input, option) =>
                String(option?.label ?? '')
                  .toLowerCase()
                  .includes(input.toLowerCase())
              }
              data-testid="user-select"
            />
          </Form.Item>

          <Form.Item
            name="permission"
            label="权限"
            rules={[{ required: true, message: '请选择权限' }]}
            initialValue="read"
          >
            <Radio.Group data-testid="permission-radio">
              <Radio value="read">读 (read)</Radio>
              <Radio value="write">写 (write)</Radio>
            </Radio.Group>
          </Form.Item>
        </Form>

        {resourceId != null && (
          <div>
            <Text strong>该资源的现有授权</Text>
            <Table<GrantSummaryItem>
              size="small"
              rowKey="grant_id"
              loading={previewQuery.isPending}
              dataSource={previewQuery.data ?? []}
              columns={previewColumns}
              pagination={false}
              locale={{ emptyText: '暂无授权' }}
              style={{ marginTop: 8 }}
              data-testid="preview-table"
            />
          </div>
        )}
      </Space>
    </Modal>
  );
}