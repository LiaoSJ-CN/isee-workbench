import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardItemEditorModal } from '../../components/DashboardItemEditorModal';
import type { DataSource, Report } from '../../types';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

const FAKE_DS: DataSource[] = [
  {
    id: 1,
    name: 'pg-main',
    db_type: 'postgresql',
    host: 'localhost',
    port: 5432,
    username: 'u',
    password: '',
    database: 'm',
    created_at: '',
    updated_at: '',
  } as unknown as DataSource,
];

const FAKE_REPORTS: Report[] = [
  {
    id: 42,
    name: 'Sales Daily',
    visibility: 'private',
    owner_user_id: 1,
    data_source_id: 1,
    items: [],
    parameters: [],
    schedule_enabled: false,
    created_at: '',
    updated_at: '',
  } as unknown as Report,
];

describe('DashboardItemEditorModal', () => {
  it('renders the title input and three tabs (report / chart / text)', () => {
    render(
      <DashboardItemEditorModal
        visible
        initialValues={{ item_type: 'report' }}
        dataSources={FAKE_DS}
        dataSourcesLoading={false}
        reports={FAKE_REPORTS}
        reportsLoading={false}
        previewColumns={[]}
        onPreviewColumns={vi.fn()}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
        submitPending={false}
      />,
      { wrapper },
    );

    expect(screen.getByText('新建看板项')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/起个名字/)).toBeInTheDocument();
    // 3 tabs — order matters because the editor renders them as separate
    // Form.Item children inside Tabs.
    expect(screen.getAllByText('报表').length).toBeGreaterThan(0);
    expect(screen.getAllByText('图表').length).toBeGreaterThan(0);
    expect(screen.getAllByText('文本').length).toBeGreaterThan(0);
  });

  it('forces every tab body to render so we never lose state when switching', () => {
    render(
      <DashboardItemEditorModal
        visible
        initialValues={{ item_type: 'chart' }}
        dataSources={FAKE_DS}
        dataSourcesLoading={false}
        reports={FAKE_REPORTS}
        reportsLoading={false}
        previewColumns={['user_id', 'created_at']}
        onPreviewColumns={vi.fn()}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
        submitPending={false}
      />,
      { wrapper },
    );

    // The chart tab exposes a 数据源 Form.Item with a label and a LIMIT
    // InputNumber. The label is rendered even when the tab is collapsed
    // because the Tabs component is forceRender=true.
    expect(screen.getByText('数据源')).toBeInTheDocument();
    expect(screen.getByText('LIMIT')).toBeInTheDocument();
  });
});
