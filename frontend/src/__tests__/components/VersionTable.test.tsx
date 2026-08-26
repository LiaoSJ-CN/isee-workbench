import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { VersionTable } from '../../pages/ReportHistory/VersionTable';

// Mock ``useUsers`` so the test is self-contained — the real hook would
// hit the network and 401 in CI. Each test customises the return shape
// via ``mockUseUsers`` below.
vi.mock('../../queries/useUsers', () => ({
  useUsers: () => mockUseUsers(),
}));

let mockUseUsers: () => { data: { id: number; username: string; role: string }[] | undefined } = () => ({
  data: undefined,
});

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

// ``report`` is required by VersionTable's Props (used by
// ``isOwnerOrAdmin`` to gate restore/delete buttons). The A2 test
// fixtures predate that change; here we hand it in explicitly.
const report = { owner_user_id: 1 };

describe('VersionTable', () => {
  it('renders version rows', () => {
    mockUseUsers = () => ({ data: undefined });
    render(
      <VersionTable
        reportId={9}
        report={report}
        versions={versions}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />,
      { wrapper },
    );
    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('init')).toBeInTheDocument();
  });

  it('shows pinned badge for pinned versions', () => {
    mockUseUsers = () => ({ data: undefined });
    render(
      <VersionTable
        reportId={9}
        report={report}
        versions={versions}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />,
      { wrapper },
    );
    expect(screen.getByText('已固定')).toBeInTheDocument();
  });

  // A3 (post-批-report-versioning): ``created_by`` is a raw user id;
  // VersionTable should resolve it to ``username`` when ``useUsers``
  // returns a populated list.
  it('renders created_by as username when the user list is loaded', () => {
    mockUseUsers = () => ({
      data: [
        { id: 1, username: 'alice', role: 'admin' },
        { id: 2, username: 'bob', role: 'editor' },
      ],
    });
    render(
      <VersionTable
        reportId={9}
        report={report}
        versions={versions}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />,
      { wrapper },
    );
    // Two rows, both created_by=1 → both cells render "alice".
    const aliceCells = screen.getAllByText('alice');
    expect(aliceCells.length).toBeGreaterThanOrEqual(2);
    // Raw id must NOT leak into the UI when a username is available.
    expect(screen.queryByText('1')).not.toBeInTheDocument();
  });

  // Fallback path: if the user list fails to load (or hasn't arrived
  // yet) the cell renders the raw id rather than crashing.
  it('falls back to raw created_by id when the user list is empty', () => {
    mockUseUsers = () => ({ data: [] });
    const loneVersion = [
      {
        id: 3,
        report_id: 9,
        version_number: 3,
        label: 'orphan',
        is_pinned: false,
        created_by: 42,
        created_at: '2026-08-20T00:00:00Z',
      },
    ];
    render(
      <VersionTable
        reportId={9}
        report={report}
        versions={loneVersion}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />,
      { wrapper },
    );
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  // Mixed scenario: one version's author is in the lookup table, the
  // other's is not (e.g. user deleted but version preserved).
  it('renders username when known and raw id when unknown', () => {
    mockUseUsers = () => ({
      data: [{ id: 1, username: 'alice', role: 'admin' }],
    });
    const mixed = [
      {
        id: 1,
        report_id: 9,
        version_number: 1,
        label: 'known',
        is_pinned: false,
        created_by: 1,
        created_at: '2026-08-01T00:00:00Z',
      },
      {
        id: 2,
        report_id: 9,
        version_number: 2,
        label: 'unknown',
        is_pinned: false,
        created_by: 999,
        created_at: '2026-08-02T00:00:00Z',
      },
    ];
    render(
      <VersionTable
        reportId={9}
        report={report}
        versions={mixed}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />,
      { wrapper },
    );
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('999')).toBeInTheDocument();
  });
});