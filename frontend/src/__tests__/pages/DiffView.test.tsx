import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ReportHistoryDiffPage from '../../pages/ReportHistory/DiffView';

const client = new QueryClient();
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>
    <MemoryRouter initialEntries={['/reports/9/history/1']}>
      <Routes>
        <Route path="/reports/:id/history/:vid" element={children as React.ReactElement} />
      </Routes>
    </MemoryRouter>
  </QueryClientProvider>
);

describe('ReportHistoryDiffPage', () => {
  it('renders without crashing', () => {
    const { container } = render(<ReportHistoryDiffPage />, { wrapper });
    expect(container).toBeTruthy();
  });
});
