import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ReportReverseLinkSection } from '../../components/ReportReverseLinkSection';
import type { DashboardRef } from '../../types';

const mocks = vi.hoisted(() => ({
  dashboardsResult: undefined as { data?: DashboardRef[]; isPending?: boolean; isError?: boolean } | undefined,
  capturedReportId: undefined as number | undefined,
}));

vi.mock('../../queries/useReports', () => ({
  useReferencingDashboards: (id: number) => {
    mocks.capturedReportId = id;
    return {
      data: mocks.dashboardsResult?.data,
      isPending: mocks.dashboardsResult?.isPending ?? false,
      isError: mocks.dashboardsResult?.isError ?? false,
    };
  },
}));

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ConfigProvider>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  </ConfigProvider>
);

const DASHBOARDS: DashboardRef[] = [
  { id: 21, name: '运营总览', visibility: 'public', item_count: 4 },
  { id: 22, name: '内部 BI', visibility: 'private', item_count: 2 },
];

describe('ReportReverseLinkSection (D 双向 link)', () => {
  it('renders card title and "0 看板" extra while pending', () => {
    mocks.dashboardsResult = { data: undefined, isPending: true };
    render(<ReportReverseLinkSection reportId={9} />, { wrapper });
    expect(screen.getByText('被引用的看板')).toBeInTheDocument();
    expect(screen.getByText(/0 个看板通过 DashboardItem 引用了本报表/)).toBeInTheDocument();
  });

  it('renders dashboard rows + per-report item count once data arrives', async () => {
    mocks.dashboardsResult = { data: DASHBOARDS, isPending: false };
    render(<ReportReverseLinkSection reportId={9} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/2 个看板通过 DashboardItem 引用了本报表/)).toBeInTheDocument();
    });
    expect(screen.getByText('运营总览')).toBeInTheDocument();
    expect(screen.getByText('内部 BI')).toBeInTheDocument();
    expect(screen.getByText('4 项引用本报表')).toBeInTheDocument();
    expect(screen.getByText('2 项引用本报表')).toBeInTheDocument();
  });

  it('renders empty state when no dashboards reference this report', () => {
    mocks.dashboardsResult = { data: [], isPending: false };
    render(<ReportReverseLinkSection reportId={9} />, { wrapper });
    expect(screen.getByText(/0 个看板通过 DashboardItem 引用了本报表/)).toBeInTheDocument();
    expect(screen.getByText('暂无看板引用')).toBeInTheDocument();
  });

  it('forwards reportId to the underlying query', () => {
    mocks.dashboardsResult = { data: [], isPending: false };
    render(<ReportReverseLinkSection reportId={99} />, { wrapper });
    expect(mocks.capturedReportId).toBe(99);
  });
});