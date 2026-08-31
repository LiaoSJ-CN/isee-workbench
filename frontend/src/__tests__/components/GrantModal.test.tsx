import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GrantModal } from '../../pages/admin/GrantModal';
import { queryKeys } from '../../queries/keys';
import type { DataSource } from '../../types';

// Pre-populate the resource list cache so the Select options appear
// without firing a real fetch. Same pattern used by other modal tests.
function makeClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ds: DataSource = {
    id: 7,
    name: 'pg-prod',
    db_type: 'postgresql',
    host: 'localhost',
    port: 5432,
    username: 'u',
    password: '',
    database: 'm',
    created_at: '',
    updated_at: '',
  } as unknown as DataSource;
  client.setQueryData(queryKeys.dataSources.list(), [ds]);
  return client;
}

const Wrapper = ({ children }: { children: React.ReactNode }) => {
  const client = makeClient();
  return (
    <ConfigProvider>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </ConfigProvider>
  );
};

describe('GrantModal', () => {
  it('renders the three-step UX: resource type / resource / user / permission', () => {
    render(
      <GrantModal
        open
        onClose={vi.fn()}
      />,
      { wrapper: Wrapper },
    );

    // Title.
    expect(screen.getByText('集中授权')).toBeInTheDocument();

    // Segmented control options.
    expect(screen.getByText('数据源')).toBeInTheDocument();
    expect(screen.getByText('报表')).toBeInTheDocument();
    expect(screen.getByText('看板')).toBeInTheDocument();

    // Form labels.
    expect(screen.getByText('资源')).toBeInTheDocument();
    expect(screen.getByText('目标用户')).toBeInTheDocument();
    expect(screen.getByText('权限')).toBeInTheDocument();

    // Permission radio defaults to read.
    expect(screen.getByLabelText(/读 \(read\)/)).toBeChecked();
  });

  it('switching resource type updates the Segmented control', () => {
    render(<GrantModal open onClose={vi.fn()} />, { wrapper: Wrapper });

    // The Segmented control exposes its options as radio inputs.
    // Default is data_source — that radio is checked.
    const dsRadio = screen.getByRole('radio', { name: '数据源' });
    const reportRadio = screen.getByRole('radio', { name: '报表' });
    expect(dsRadio).toBeChecked();
    expect(reportRadio).not.toBeChecked();

    // Click 报表 — that radio becomes checked, the DS one no longer is.
    fireEvent.click(reportRadio);
    expect(reportRadio).toBeChecked();
    expect(dsRadio).not.toBeChecked();
  });
});