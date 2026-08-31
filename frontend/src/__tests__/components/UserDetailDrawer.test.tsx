import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { UserDetailDrawer } from '../../pages/admin/UserDetailDrawer';
import { queryKeys } from '../../queries/keys';
import type { GrantSummaryItem, UserResponse } from '../../types';

// Pre-populate the query cache so the drawer's useAdminUserGrants
// hits cache and never fires. Same pattern used by other drawer
// modal tests in this repo.
function makeClient(userId: number, grants: GrantSummaryItem[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(queryKeys.adminUsers.grants(userId), {
    subject_type: 'user',
    subject_id: userId,
    grants,
  });
  return client;
}

const wrapper = (client: QueryClient) =>
  ({ children }: { children: React.ReactNode }) => (
    <ConfigProvider>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </ConfigProvider>
  );

const FAKE_USER: UserResponse = {
  id: 5,
  username: 'alice',
  role: 'viewer',
  disabled: false,
};

const FAKE_GRANTS: GrantSummaryItem[] = [
  {
    resource_type: 'data_source',
    resource_id: 100,
    resource_name: 'pg-prod',
    grant_id: 1,
    permission: 'read',
    granted_by: 1,
    granted_by_username: 'admin',
    created_at: '2026-08-30T10:00:00Z',
  },
  {
    resource_type: 'report',
    resource_id: 200,
    resource_name: 'Sales Daily',
    grant_id: 2,
    permission: 'write',
    granted_by: 1,
    granted_by_username: 'admin',
    created_at: '2026-08-30T10:05:00Z',
  },
];

describe('UserDetailDrawer', () => {
  it('renders 4 tabs with the user identity in the header', () => {
    const client = makeClient(FAKE_USER.id, FAKE_GRANTS);

    render(
      <UserDetailDrawer
        open
        user={FAKE_USER}
        currentUserId={1}
        onResetPassword={vi.fn()}
        onClose={vi.fn()}
      />,
      { wrapper: wrapper(client) },
    );

    // Drawer title carries the username (also rendered in the basic
    // info descriptions below — we only need to confirm it's there).
    expect(screen.getAllByText(FAKE_USER.username).length).toBeGreaterThanOrEqual(1);
    // Four tab labels visible (AntD renders them in the tab list).
    expect(screen.getByRole('tab', { name: '基本信息' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /数据源授权/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /报表授权/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /看板授权/ })).toBeInTheDocument();
  });

  it('basic-info tab shows read-only fields for id / username / role / status / timestamps', () => {
    const client = makeClient(FAKE_USER.id, []);

    render(
      <UserDetailDrawer
        open
        user={FAKE_USER}
        currentUserId={1}
        onResetPassword={vi.fn()}
        onClose={vi.fn()}
      />,
      { wrapper: wrapper(client) },
    );

    // Descriptions renders label / value pairs.
    expect(screen.getByText('ID')).toBeInTheDocument();
    expect(screen.getByText(String(FAKE_USER.id))).toBeInTheDocument();
    expect(screen.getByText('用户名')).toBeInTheDocument();
    // Username appears twice (drawer title + descriptions); use getAllByText.
    expect(screen.getAllByText(FAKE_USER.username).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('角色')).toBeInTheDocument();
    expect(screen.getByText('状态')).toBeInTheDocument();
    expect(screen.getByText('创建时间')).toBeInTheDocument();
    expect(screen.getByText('最近登录')).toBeInTheDocument();
  });

  it('basic-info tab shows the DS-grant counts in the tab labels', () => {
    const client = makeClient(FAKE_USER.id, FAKE_GRANTS);

    render(
      <UserDetailDrawer
        open
        user={FAKE_USER}
        currentUserId={1}
        onResetPassword={vi.fn()}
        onClose={vi.fn()}
      />,
      { wrapper: wrapper(client) },
    );

    // Each resource tab label embeds the count of grants in that
    // category — the drawer computes them client-side from the
    // cached grants payload. We have 1 DS grant + 1 Report grant +
    // 0 Dashboard grants.
    expect(screen.getByRole('tab', { name: /数据源授权 \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /报表授权 \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /看板授权 \(0\)/ })).toBeInTheDocument();
  });

  it('disables disable/enable button when currentUserId === user.id (self-edit guard)', () => {
    const client = makeClient(FAKE_USER.id, []);

    render(
      <UserDetailDrawer
        open
        user={FAKE_USER}
        currentUserId={FAKE_USER.id}
        onResetPassword={vi.fn()}
        onClose={vi.fn()}
      />,
      { wrapper: wrapper(client) },
    );

    // The Drawer "extra" area has the disable button — it should be
    // disabled when the operator is looking at their own row.
    const disableBtn = screen.getByTestId('drawer-disable');
    expect(disableBtn).toBeDisabled();
  });
});