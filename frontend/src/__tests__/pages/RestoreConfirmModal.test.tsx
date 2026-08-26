import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RestoreConfirmModal } from '../../pages/ReportHistory/RestoreConfirmModal';

// Mock ``useRestoreReportVersion`` so each test can control success
// vs. failure without touching the real network. The capture object
// also lets us assert what payload was sent (A5: the
// ``expectedUpdatedAt`` field must be threaded through).
let mockMutateAsync: (payload: unknown) => Promise<unknown> = vi.fn();
let captured: unknown = null;
vi.mock('../../queries/useReportVersions', () => ({
  useRestoreReportVersion: () => ({
    mutateAsync: (payload: unknown) => {
      captured = payload;
      return mockMutateAsync(payload);
    },
    isPending: false,
  }),
}));

// antd's static ``message.warning`` / ``message.error`` aren't easy
// to introspect via the DOM; spy on them instead.
import { message } from 'antd';

const client = new QueryClient();
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

const version = {
  id: 7,
  report_id: 9,
  version_number: 3,
  label: 'snapshot',
  is_pinned: false,
  created_by: 1,
  created_at: '2026-08-01T00:00:00Z',
};

describe('RestoreConfirmModal (A5 optimistic lock)', () => {
  it('forwards currentUpdatedAt to the mutation payload', async () => {
    mockMutateAsync = vi.fn().mockResolvedValue({});
    const warnSpy = vi.spyOn(message, 'warning');
    captured = null;
    render(
      <RestoreConfirmModal
        open
        reportId={9}
        version={version}
        currentUpdatedAt="2026-08-26T12:00:00+00:00"
        onClose={vi.fn()}
        onRestored={vi.fn()}
      />,
      { wrapper },
    );
    fireEvent.click(screen.getByRole('button', { name: '确认恢复' }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toEqual({
      versionId: 7,
      expectedUpdatedAt: '2026-08-26T12:00:00+00:00',
    });
    warnSpy.mockRestore();
  });

  it('sends null expectedUpdatedAt when not provided', async () => {
    mockMutateAsync = vi.fn().mockResolvedValue({});
    captured = null;
    render(<RestoreConfirmModal open reportId={9} version={version} onClose={vi.fn()} />, {
      wrapper,
    });
    fireEvent.click(screen.getByRole('button', { name: '确认恢复' }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toEqual({ versionId: 7, expectedUpdatedAt: null });
  });

  it('surfaces 409 via message.warning + invalidates the report queries', async () => {
    mockMutateAsync = vi.fn().mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            message: 'Report was modified since you loaded it; refresh and try again',
            current_updated_at: '2026-08-26T13:00:00+00:00',
          },
        },
      },
    });
    const warnSpy = vi.spyOn(message, 'warning');
    const errorSpy = vi.spyOn(message, 'error');
    render(
      <RestoreConfirmModal
        open
        reportId={9}
        version={version}
        currentUpdatedAt="2026-08-26T12:00:00+00:00"
        onClose={vi.fn()}
      />,
      { wrapper },
    );
    fireEvent.click(screen.getByRole('button', { name: '确认恢复' }));
    await waitFor(() =>
      expect(warnSpy).toHaveBeenCalledWith(
        'Report was modified since you loaded it; refresh and try again',
      ),
    );
    // Generic error path must NOT fire for 409.
    expect(errorSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it('falls back to message.error for non-409 failures', async () => {
    mockMutateAsync = vi.fn().mockRejectedValue({
      response: { status: 500, data: { detail: 'kaboom' } },
    });
    const errorSpy = vi.spyOn(message, 'error');
    render(<RestoreConfirmModal open reportId={9} version={version} onClose={vi.fn()} />, {
      wrapper,
    });
    fireEvent.click(screen.getByRole('button', { name: '确认恢复' }));
    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith('恢复失败'));
    errorSpy.mockRestore();
  });
});
