import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { DataSourceReferencesPanel } from '../../components/DataSourceReferencesPanel';
import type { DashboardRef, ReportRef } from '../../types';

// Mock both reverse-link queries — the panel only cares about the
// result shape, not the network. ``vi.hoisted`` keeps the
// ``let capturedId`` mutable reference in scope for the mock factory.
const mocks = vi.hoisted(() => ({
  reportsResult: undefined as { data?: ReportRef[]; isPending?: boolean; isError?: boolean } | undefined,
  dashboardsResult: undefined as { data?: DashboardRef[]; isPending?: boolean; isError?: boolean } | undefined,
  capturedDsId: undefined as number | undefined,
}));

vi.mock('../../queries/useDataSources', () => ({
  useReferencingReports: (id: number) => {
    mocks.capturedDsId = id;
    return {
      data: mocks.reportsResult?.data,
      isPending: mocks.reportsResult?.isPending ?? false,
      isError: mocks.reportsResult?.isError ?? false,
    };
  },
  useReferencingDashboards: () => ({
    data: mocks.dashboardsResult?.data,
    isPending: mocks.dashboardsResult?.isPending ?? false,
    isError: mocks.dashboardsResult?.isError ?? false,
  }),
}));

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ConfigProvider>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  </ConfigProvider>
);

const REPORTS: ReportRef[] = [
  { id: 11, name: '月销售报表', visibility: 'public', is_active: true },
  { id: 12, name: '日活看板', visibility: 'private', is_active: false },
];

const DASHBOARDS: DashboardRef[] = [
  { id: 21, name: '运营总览', visibility: 'public', item_count: 3 },
  { id: 22, name: '内部 BI', visibility: 'org', item_count: 1 },
];

describe('DataSourceReferencesPanel (D 双向 link)', () => {
  it('renders the panel header with 0 counts while pending', () => {
    mocks.reportsResult = { data: undefined, isPending: true };
    mocks.dashboardsResult = { data: undefined, isPending: true };
    render(<DataSourceReferencesPanel dataSourceId={7} />, { wrapper });
    // Headings always render; the count is 0 before data arrives.
    expect(screen.getByText(/引用的报表 \(0\)/)).toBeInTheDocument();
    expect(screen.getByText(/引用的看板 \(0\)/)).toBeInTheDocument();
  });

  it('renders report and dashboard rows once data arrives', async () => {
    mocks.reportsResult = { data: REPORTS, isPending: false };
    mocks.dashboardsResult = { data: DASHBOARDS, isPending: false };
    render(<DataSourceReferencesPanel dataSourceId={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/引用的报表 \(2\)/)).toBeInTheDocument();
      expect(screen.getByText(/引用的看板 \(2\)/)).toBeInTheDocument();
    });
    expect(screen.getByText('月销售报表')).toBeInTheDocument();
    expect(screen.getByText('日活看板')).toBeInTheDocument();
    expect(screen.getByText('运营总览')).toBeInTheDocument();
    expect(screen.getByText('内部 BI')).toBeInTheDocument();
    // Visibility tags surface the ACL tier at a glance. Test data has
    // 2× 公开 (one report + one dashboard), 1× 私有 (private report),
    // 1× 同部门 (org dashboard) — assert by count rather than
    // exact match so adding more rows doesn't break the test.
    expect(screen.getAllByText('公开').length).toBe(2);
    expect(screen.getAllByText('私有').length).toBe(1);
    expect(screen.getAllByText('同部门').length).toBe(1);
    // is_active=false surfaces a "已停用" warning tag.
    expect(screen.getByText('已停用')).toBeInTheDocument();
    // Dashboard item_count renders as "{N} 项".
    expect(screen.getByText('3 项')).toBeInTheDocument();
  });

  it('renders empty states when neither list has rows', () => {
    mocks.reportsResult = { data: [], isPending: false };
    mocks.dashboardsResult = { data: [], isPending: false };
    render(<DataSourceReferencesPanel dataSourceId={7} />, { wrapper });
    expect(screen.getByText(/引用的报表 \(0\)/)).toBeInTheDocument();
    expect(screen.getByText(/引用的看板 \(0\)/)).toBeInTheDocument();
    expect(screen.getAllByText(/暂无.*引用/)).toHaveLength(2);
  });

  it('forwards the dataSourceId to the underlying queries', () => {
    mocks.reportsResult = { data: [], isPending: false };
    mocks.dashboardsResult = { data: [], isPending: false };
    render(<DataSourceReferencesPanel dataSourceId={42} />, { wrapper });
    expect(mocks.capturedDsId).toBe(42);
  });
});