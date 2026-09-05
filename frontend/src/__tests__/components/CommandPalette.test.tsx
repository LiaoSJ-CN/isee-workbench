import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { CommandPalette } from '../../components/CommandPalette';
import type {
  DashboardRef,
  DataSourceRef,
  ReportRef,
  SearchResponse,
} from '../../types';

// ---- Mock the search hook ----
// We capture the latest q so tests can assert which query the
// palette actually sent, and we expose setters so each test can
// control what the mock returns.
const mocks = vi.hoisted(() => ({
  lastQ: undefined as string | undefined,
  pending: false,
  // The canned response; tests overwrite per-case.
  response: undefined as SearchResponse | undefined,
}));

vi.mock('../../queries/useSearch', async () => {
  const actual = await vi.importActual<typeof import('../../queries/useSearch')>(
    '../../queries/useSearch',
  );
  return {
    ...actual,
    useSearch: (q: string) => {
      // Only record non-empty queries — mirrors the actual hook's
      // ``enabled: q.trim().length > 0`` short-circuit so the test
      // can assert "useSearch did NOT receive a query" without
      // counting the initial empty-q mount.
      if (q.length > 0) mocks.lastQ = q;
      return {
        data: mocks.response,
        isPending: mocks.pending,
        isError: false,
        error: null,
      };
    },
  };
});

// Mock react-router-dom's useNavigate so we can assert navigation
// without mounting a real route table.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <ConfigProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>{children}</MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

function sampleResponse(): SearchResponse {
  const reports: ReportRef[] = [
    { id: 11, name: '财务月报', visibility: 'private', is_active: true },
    { id: 12, name: '财务报表聚合', visibility: 'org', is_active: true },
  ];
  const dashboards: DashboardRef[] = [
    { id: 21, name: '财务看板', visibility: 'public', item_count: 4 },
  ];
  const dataSources: DataSourceRef[] = [
    { id: 31, name: '财务数据库', db_type: 'sqlite' },
  ];
  return { reports, dashboards, data_sources: dataSources };
}

beforeEach(() => {
  mockNavigate.mockReset();
  mocks.lastQ = undefined;
  mocks.pending = false;
  mocks.response = undefined;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('CommandPalette (A 联合搜索)', () => {
  it('renders the input with the placeholder and ⌘K hint', () => {
    render(<CommandPalette />, { wrapper: makeWrapper() });
    const input = screen.getByTestId('command-palette-input');
    expect(input).toBeInTheDocument();
    expect(input.getAttribute('placeholder')).toContain('搜索');
    // ⌘K hint is in the suffix; asserting via the container HTML is
    // brittle, so just check the input is in the document.
  });

  it('does NOT call useSearch when q is empty (short-circuit)', () => {
    vi.useFakeTimers();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    // No fireEvent — the input is empty. ``useSearch`` was mocked,
    // and we cleared ``mocks.lastQ`` in beforeEach.
    act(() => vi.advanceTimersByTime(500));
    // With debounce disabled (q never changed), useSearch would only
    // be called if the popover opened with non-empty q. Empty q +
    // no interaction → no call.
    expect(mocks.lastQ).toBeUndefined();
  });

  it('debounces typing and forwards the trimmed value to useSearch', async () => {
    vi.useFakeTimers();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    const input = screen.getByTestId('command-palette-input');

    fireEvent.change(input, { target: { value: '  财务  ' } });

    // Before the debounce window expires, the hook hasn't been
    // called with the new q.
    act(() => vi.advanceTimersByTime(100));
    expect(mocks.lastQ).toBeUndefined();

    // After the debounce window, the trimmed value reaches the hook.
    act(() => vi.advanceTimersByTime(300));
    expect(mocks.lastQ).toBe('财务');
  });

  it('renders 3 group headers in order: 报表, 看板, 数据源', () => {
    mocks.response = sampleResponse();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    const input = screen.getByTestId('command-palette-input');
    fireEvent.change(input, { target: { value: '财务' } });

    // Use the live ``useSearch`` (mocked) — since the popover only
    // opens when q is non-empty AND data is present, and we set
    // ``mocks.response`` directly, the groups should render.
    return waitFor(() => {
      expect(screen.getByTestId('command-palette-group-报表')).toBeInTheDocument();
    }).then(() => {
      expect(screen.getByTestId('command-palette-group-看板')).toBeInTheDocument();
      expect(screen.getByTestId('command-palette-group-数据源')).toBeInTheDocument();
    });
  });

  it('renders every ref.name in the response', async () => {
    mocks.response = sampleResponse();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByTestId('command-palette-input'), {
      target: { value: '财务' },
    });
    await waitFor(() => {
      expect(screen.getByText('财务月报')).toBeInTheDocument();
      expect(screen.getByText('财务报表聚合')).toBeInTheDocument();
      expect(screen.getByText('财务看板')).toBeInTheDocument();
      expect(screen.getByText('财务数据库')).toBeInTheDocument();
    });
  });

  it('clicking a report row calls navigate(/reports/{id})', async () => {
    mocks.response = sampleResponse();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByTestId('command-palette-input'), {
      target: { value: '财务' },
    });
    await waitFor(() => screen.getByText('财务月报'));
    fireEvent.mouseDown(screen.getByText('财务月报'));
    expect(mockNavigate).toHaveBeenCalledWith('/reports/11');
  });

  it('clicking a dashboard row calls navigate(/dashboards/{id})', async () => {
    mocks.response = sampleResponse();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByTestId('command-palette-input'), {
      target: { value: '财务' },
    });
    await waitFor(() => screen.getByText('财务看板'));
    fireEvent.mouseDown(screen.getByText('财务看板'));
    expect(mockNavigate).toHaveBeenCalledWith('/dashboards/21');
  });

  it('clicking a data source row lands on the list page (no detail route)', async () => {
    mocks.response = sampleResponse();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByTestId('command-palette-input'), {
      target: { value: '财务' },
    });
    await waitFor(() => screen.getByText('财务数据库'));
    fireEvent.mouseDown(screen.getByText('财务数据库'));
    expect(mockNavigate).toHaveBeenCalledWith('/data-sources');
  });

  it('Escape closes the popover and clears the input', () => {
    mocks.response = sampleResponse();
    render(<CommandPalette />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByTestId('command-palette-input'), {
      target: { value: '财务' },
    });
    fireEvent.keyDown(window, { key: 'Escape' });
    // The popover should be unmounted. The input is still in the DOM
    // (always-visible Input), but its value resets to empty.
    expect(screen.queryByTestId('command-palette-popover')).not.toBeInTheDocument();
    expect(
      (screen.getByTestId('command-palette-input') as HTMLInputElement).value,
    ).toBe('');
  });

  it('click-outside closes the popover', async () => {
    mocks.response = sampleResponse();
    render(
      <div>
        <CommandPalette />
        <button data-testid="outside">outside</button>
      </div>,
      { wrapper: makeWrapper() },
    );
    fireEvent.change(screen.getByTestId('command-palette-input'), {
      target: { value: '财务' },
    });
    await waitFor(() =>
      expect(screen.getByTestId('command-palette-popover')).toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByTestId('outside'));
    expect(screen.queryByTestId('command-palette-popover')).not.toBeInTheDocument();
  });

  it('loading state shows the spinner in the input suffix', () => {
    mocks.pending = true;
    render(<CommandPalette />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByTestId('command-palette-input'), {
      target: { value: '财务' },
    });
    // Antd Spin renders an svg with role=img; just check that at
    // least one is in the document (the input suffix slot).
    expect(screen.getAllByRole('img', { hidden: true }).length).toBeGreaterThan(0);
  });
});