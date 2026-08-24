/** Tests for AuditLogPage — focus on the filter form wiring (P3-1).

The page is admin-only and depends on ``useAuditLogs`` (React Query)
and ``useUsers``. We mock both so the test exercises only the form →
filter object → query trigger path; no network, no router.

The ``request_id`` and ``ip_address`` quick filters were added on top
of the existing form — these tests lock in their wiring so a future
refactor doesn't silently drop them.
*/

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockUseAuditLogs = vi.fn();
const mockUseUsers = vi.fn();

vi.mock('../../queries/useAuditLog', () => ({
  useAuditLogs: (filters?: unknown) => mockUseAuditLogs(filters),
}));

vi.mock('../../queries/useDataSources', () => ({
  useUsers: () => mockUseUsers(),
}));

vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
  };
});

import AuditLogPage from '../../pages/AuditLogPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuditLogPage />
    </QueryClientProvider>,
  );
}

describe('AuditLogPage filter wiring', () => {
  beforeEach(() => {
    mockUseAuditLogs.mockReset();
    mockUseUsers.mockReset();
    mockUseAuditLogs.mockReturnValue({
      data: { items: [], total: 0, limit: 20, offset: 0 },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseUsers.mockReturnValue({ data: [], isPending: false });
  });

  it('renders request_id and ip_address quick-filter inputs', () => {
    renderPage();
    // antd ``Input`` with placeholder renders as a textbox with that
    // placeholder; both new fields expose placeholders so admins can
    // see at a glance what kind of value to type.
    expect(screen.getByPlaceholderText(/abc12345/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/10\.0\.0\.5/)).toBeInTheDocument();
  });

  it('sends request_id through to the API client when set', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByPlaceholderText(/abc12345/), 'deadbeefcafe1234');
    await user.click(screen.getByRole('button', { name: /查询/ }));

    await waitFor(() => {
      expect(mockUseAuditLogs).toHaveBeenCalledWith(
        expect.objectContaining({ request_id: 'deadbeefcafe1234' }),
      );
    });
  });

  it('sends ip_address through to the API client when set', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByPlaceholderText(/10\.0\.0\.5/), '192.168.1.42');
    await user.click(screen.getByRole('button', { name: /查询/ }));

    await waitFor(() => {
      expect(mockUseAuditLogs).toHaveBeenCalledWith(
        expect.objectContaining({ ip_address: '192.168.1.42' }),
      );
    });
  });

  it('omits request_id / ip_address when fields are blank', async () => {
    const user = userEvent.setup();
    renderPage();

    // No input — click 查询 with all fields blank.
    await user.click(screen.getByRole('button', { name: /查询/ }));

    await waitFor(() => {
      const lastCall = mockUseAuditLogs.mock.calls.at(-1)?.[0] as
        Record<string, unknown> | undefined;
      expect(lastCall).toBeDefined();
      expect(lastCall).not.toHaveProperty('request_id');
      expect(lastCall).not.toHaveProperty('ip_address');
    });
  });
});
