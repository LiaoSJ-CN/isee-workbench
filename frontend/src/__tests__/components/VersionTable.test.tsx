import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { VersionTable } from '../../pages/ReportHistory/VersionTable';

const client = new QueryClient();
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>
    <MemoryRouter>{children}</MemoryRouter>
  </QueryClientProvider>
);

const versions = [
  {
    id: 1,
    report_id: 9,
    version_number: 1,
    label: 'init',
    is_pinned: true,
    created_by: 1,
    created_at: '2026-08-01T00:00:00Z',
  },
  {
    id: 2,
    report_id: 9,
    version_number: 2,
    label: null,
    is_pinned: false,
    created_by: 1,
    created_at: '2026-08-15T00:00:00Z',
  },
];

describe('VersionTable', () => {
  it('renders version rows', () => {
    render(
      <VersionTable reportId={9} versions={versions} onRestore={vi.fn()} onDelete={vi.fn()} />,
      { wrapper },
    );
    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('init')).toBeInTheDocument();
  });

  it('shows pinned badge for pinned versions', () => {
    render(
      <VersionTable reportId={9} versions={versions} onRestore={vi.fn()} onDelete={vi.fn()} />,
      { wrapper },
    );
    expect(screen.getByText('已固定')).toBeInTheDocument();
  });
});
