/**
 * Tests for VersionTable's pin button (B post-批-report-versioning).
 *
 * Mocks React Query hooks + current user so the test exercises only the
 * pin-button wiring — no network, no router. The render test scans
 * ``data-testid`` rather than the antd Table role selectors because
 * happy-dom + antd Table's virtualized cells are flaky to query via
 * ``getByRole``.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { VersionTable } from '../../pages/ReportHistory/VersionTable';

// Capture mutateAsync calls so tests can assert what was sent.
let mockMutateAsync: (payload: unknown) => Promise<unknown> = vi.fn();
let mockIsPending = false;
let captured: unknown = null;
const mockUsePin = vi.fn(() => ({
  mutateAsync: (payload: unknown) => {
    captured = payload;
    return mockMutateAsync(payload);
  },
  isPending: mockIsPending,
}));

vi.mock('../../queries/useReportVersions', () => ({
  usePinReportVersion: () => mockUsePin(),
}));

let mockCurrentUser: { id: number; username: string; role: string } | null = null;
vi.mock('../../queries/useCurrentUser', () => ({
  // Mirror the real hook shape — a UseQueryResult wrapper with a
  // ``data`` field, not the bare user object. ``isOwnerOrAdmin`` reads
  // ``user.data.role`` so the mock must keep the wrapper too.
  useCurrentUser: () => ({ data: mockCurrentUser, isPending: false }),
  isOwnerOrAdmin: (
    user: { data: { id: number; username: string; role: string } | null } | null,
  ): boolean => {
    if (!user?.data) return false;
    return user.data.role === 'admin';
  },
}));

const mockUseUsers = vi.fn(() => ({ data: [] }));
vi.mock('../../queries/useUsers', () => ({
  useUsers: () => mockUseUsers(),
}));

import { message } from 'antd';

const versions = [
  {
    id: 1,
    report_id: 9,
    version_number: 3,
    label: 'Q1 报表',
    is_pinned: false,
    created_by: 1,
    created_at: '2026-08-01T00:00:00Z',
  },
  {
    id: 2,
    report_id: 9,
    version_number: 2,
    label: null,
    is_pinned: true,
    created_by: 1,
    created_at: '2026-07-15T00:00:00Z',
  },
];

const report = { owner_user_id: 1 };

function renderTable() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <VersionTable
          reportId={9}
          report={report}
          versions={versions}
          onRestore={vi.fn()}
          onDelete={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('VersionTable pin button', () => {
  beforeEach(() => {
    captured = null;
    mockMutateAsync = vi.fn().mockResolvedValue({});
    mockIsPending = false;
    mockUseUsers.mockReturnValue({ data: [] });
  });

  it('admin sees "固定" for an unpinned row; click fires mutation with pinned=true', async () => {
    mockCurrentUser = { id: 1, username: 'admin', role: 'admin' };
    renderTable();

    // Query by the text span and walk up to the enclosing button.
    // ``getByRole`` with ``name`` compares the *accessible name*,
    // which here includes the icon's ``aria-label="pushpin"``, so
    // a regex on the accessible name alone won't match.
    const pinBtn = await screen.findByText(/^固\s*定$/);
    fireEvent.click(pinBtn.closest('button')!);

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toEqual({ versionId: 1, pinned: true });
  });

  it('admin sees "取消固定" for a pinned row; click fires mutation with pinned=false', async () => {
    mockCurrentUser = { id: 1, username: 'admin', role: 'admin' };
    renderTable();

    const unpinBtn = await screen.findByText(/^取消\s*固定$/);
    fireEvent.click(unpinBtn.closest('button')!);

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toEqual({ versionId: 2, pinned: false });
  });

  it('non-admin editor sees pin buttons disabled', async () => {
    // ``isOwnerOrAdmin`` mock returns true only for admin role; an
    // editor who is not the owner should see the pin button disabled.
    mockCurrentUser = { id: 99, username: 'bob', role: 'editor' };
    renderTable();

    const pinText = await screen.findByText(/^固\s*定$/);
    expect(pinText.closest('button')!).toBeDisabled();
  });

  it('shows error message when the mutation rejects', async () => {
    mockCurrentUser = { id: 1, username: 'admin', role: 'admin' };
    mockMutateAsync = vi.fn().mockRejectedValue(new Error('kaboom'));
    const errorSpy = vi.spyOn(message, 'error');

    renderTable();
    const pinText = await screen.findByText(/^固\s*定$/);
    fireEvent.click(pinText.closest('button')!);

    await waitFor(() => expect(errorSpy).toHaveBeenCalled());
    errorSpy.mockRestore();
  });
});
