import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SaveVersionModal } from '../../components/SaveVersionModal';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

describe('SaveVersionModal', () => {
  it('renders with empty label', () => {
    render(<SaveVersionModal open reportId={1} onClose={vi.fn()} />, { wrapper });
    expect(screen.getByText('保存为版本')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/留空将自动/)).toBeInTheDocument();
  });

  it('calls onClose when cancel clicked', () => {
    const onClose = vi.fn();
    render(<SaveVersionModal open reportId={1} onClose={onClose} />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: /取\s*消/ }));
    expect(onClose).toHaveBeenCalled();
  });
});
