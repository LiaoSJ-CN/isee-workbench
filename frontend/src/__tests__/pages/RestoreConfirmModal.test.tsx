import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RestoreConfirmModal } from '../../pages/ReportHistory/RestoreConfirmModal';

const client = new QueryClient();
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

describe('RestoreConfirmModal', () => {
  it('does not render content when version is null', () => {
    render(<RestoreConfirmModal open reportId={9} version={null} onClose={vi.fn()} />, { wrapper });
    // Modal shell is mounted but content is gated on `version` being non-null
    expect(screen.queryByText(/确认恢复到/)).toBeNull();
  });
});
