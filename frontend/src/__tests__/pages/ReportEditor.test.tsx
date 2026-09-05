/** RTL component test for ReportEditor (TODO-4 regression).
 *
 *  Locks in the behaviour that ``itemsView`` and the ``报表项 (N)`` tab
 *  badge re-render when the React Query cache changes — i.e. the
 *  component no longer relies on a once-hydrated local ``buffer`` copy.
 *  We pre-populate the cache and then simulate what the item-mutation
 *  hooks' optimistic ``onMutate`` callbacks do (setQueryData), which is
 *  the exact code path that was previously leaving the tab stale.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ReportEditor from '../../pages/ReportEditor';
import { queryKeys } from '../../queries/keys';
import type { Report, ReportItem } from '../../types';

// ---- Mocks ---------------------------------------------------------------

// ``useMe`` is the gate for the "另存为模板" button + the isOwner
// flag. Stub to an admin so we render the full toolbar.
vi.mock('../../queries/useAuth', () => ({
  useMe: () => ({
    data: { user_id: 1, username: 'admin', role: 'admin' },
  }),
  useLogin: () => ({ mutate: vi.fn(), isPending: false }),
  useLogout: () => ({ mutate: vi.fn(), isPending: false }),
}));

// ``useDataSources`` would otherwise hit ``dataSourceApi.list`` and 401
// in CI. Pre-load via the cache instead (see setup()).
vi.mock('../../queries/useDataSources', () => ({
  useDataSources: () => ({ data: [], isPending: false }),
}));

vi.mock('../../queries/useReportVersions', () => ({
  useReportVersions: () => ({ data: [], isPending: false }),
  useReportVersion: () => ({ data: undefined, isPending: false }),
  useReportVersionDiff: () => ({ data: undefined, isPending: false }),
  useCreateReportVersion: () => ({ mutate: vi.fn(), isPending: false }),
  useRestoreReportVersion: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteReportVersion: () => ({ mutate: vi.fn(), isPending: false }),
  usePinReportVersion: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../queries/useReportTemplates', () => ({
  useSaveAsTemplate: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../queries/useParameters', () => ({
  useReportParameters: () => ({ data: [], isPending: false }),
  useCreateReportParameter: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateReportParameter: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteReportParameter: () => ({ mutate: vi.fn(), isPending: false }),
}));

// NOTE: ``useReport`` / item mutations live in useReports and are used
// REAL. We pre-populate the cache so useReport's queryFn is never
// called, and the mutations don't fire unless triggered explicitly.

// ---- Fixtures ------------------------------------------------------------

function makeItem(id: number, order_index: number, name?: string): ReportItem {
  return {
    id,
    report_id: 9,
    name: name ?? `Item ${id}`,
    item_type: 'table',
    order_index,
    table_name: 'foo',
    fields: [],
    where_conditions: [],
    group_by: [],
    order_by: [],
    display_config: {},
    limit: 100,
  };
}

function makeReport(items: ReportItem[]): Report {
  return {
    id: 9,
    name: 'Test Report',
    description: 'desc',
    data_source_id: 1,
    is_active: true,
    is_scheduled: false,
    output_formats: ['html'],
    owner_user_id: 1,
    visibility: 'private',
    items,
  };
}

// ---- Tests ---------------------------------------------------------------

describe('ReportEditor — items derive from server cache', () => {
  let client: QueryClient;

  beforeEach(() => {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  function setup(report: Report): QueryClient {
    client.setQueryData(queryKeys.reports.detail(report.id), report);
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/reports/${report.id}`]}>
          <Routes>
            <Route path="/reports/:id" element={<ReportEditor />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return client;
  }

  it('renders the initial item count in the tab badge', async () => {
    setup(
      makeReport([
        makeItem(1, 0, 'Alpha'),
        makeItem(2, 1, 'Beta'),
        makeItem(3, 2, 'Gamma'),
      ]),
    );
    await waitFor(() =>
      expect(screen.getByText('报表项 (3)')).toBeInTheDocument(),
    );
  });

  it('renders every item name from the cache after entering the items tab', async () => {
    // Items live behind a tab; antd's default is to mount inactive
    // panels but we explicitly activate it to avoid relying on that
    // internal detail.
    setup(
      makeReport([
        makeItem(1, 0, 'Alpha'),
        makeItem(2, 1, 'Beta'),
        makeItem(3, 2, 'Gamma'),
      ]),
    );
    await waitFor(() =>
      expect(screen.getByText('报表项 (3)')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('报表项 (3)'));
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.getByText('Gamma')).toBeInTheDocument();
  });

  it('re-renders when items change in the cache (TODO-4 regression)', async () => {
    // The previous design hydrated a local ``buffer`` once and never
    // re-synced it. After every cache mutation the tab badge and the
    // items list went stale until the next page load. This test fails
    // on the old code and passes once itemsView derives from
    // ``report.items`` (the cache).
    setup(
      makeReport([
        makeItem(1, 0, 'Alpha'),
        makeItem(2, 1, 'Beta'),
        makeItem(3, 2, 'Gamma'),
      ]),
    );
    await waitFor(() =>
      expect(screen.getByText('报表项 (3)')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('报表项 (3)'));

    // Simulate the exact cache patch that
    // ``useDeleteReportItem.onMutate`` performs.
    const cached = client.getQueryData<Report>(queryKeys.reports.detail(9));
    expect(cached).toBeDefined();
    client.setQueryData<Report>(queryKeys.reports.detail(9), {
      ...cached!,
      items: cached!.items.filter((i) => i.id !== 2),
    });

    // Both the badge AND the rendered list must update — that's the
    // bug we fixed.
    await waitFor(() =>
      expect(screen.getByText('报表项 (2)')).toBeInTheDocument(),
    );
    expect(screen.queryByText('Beta')).not.toBeInTheDocument();
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Gamma')).toBeInTheDocument();
  });

  it('re-renders when an item is added in the cache', async () => {
    // Symmetric to delete: the optimistic create appends a temp-id
    // item, and the badge + list should react without touching
    // local buffer state.
    setup(makeReport([makeItem(1, 0, 'Alpha')]));
    await waitFor(() =>
      expect(screen.getByText('报表项 (1)')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('报表项 (1)'));

    const cached = client.getQueryData<Report>(queryKeys.reports.detail(9))!;
    const newItem = makeItem(-Date.now(), 1, 'Newly added');
    client.setQueryData<Report>(queryKeys.reports.detail(9), {
      ...cached,
      items: [...cached.items, newItem],
    });

    await waitFor(() =>
      expect(screen.getByText('报表项 (2)')).toBeInTheDocument(),
    );
    expect(screen.getByText('Newly added')).toBeInTheDocument();
  });
});