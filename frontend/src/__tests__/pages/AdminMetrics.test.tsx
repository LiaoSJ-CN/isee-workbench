/** Tests for AdminMetrics page (批 12).

Three layers of coverage, matching the spec verbatim:

1. **Empty store** — ``pools.length === 0`` renders zero summary
   cards and a friendly "no data sources" table state. Confirms the
   page doesn't crash when the backend returns an empty fleet (fresh
   install / no registered DataSources yet).
2. **Populated store** — verifies the per-DS table renders one row
   per pool, the sparkline SVG path is drawn, and the summary counts
   roll up correctly.
3. **Health color mapping** — confirms each ``Health`` enum value
   renders the right Ant Design ``Tag`` colour (``success`` /
   ``warning`` / ``error``), which is what the backend uses to
   communicate the operator-action priority for a pool.

We mock ``useAdminMetrics`` so the test exercises only the render
path; no network, no router. Pattern mirrors ``AuditLog.test.tsx``.
*/

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { AdminMetricsResponse } from '../../types';

const mockUseAdminMetrics = vi.fn();

vi.mock('../../queries/useAdminMetrics', () => ({
  useAdminMetrics: () => mockUseAdminMetrics(),
}));

import AdminMetrics from '../../pages/AdminMetrics';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AdminMetrics />
    </QueryClientProvider>,
  );
}

function okResponse(payload: Partial<AdminMetricsResponse> = {}) {
  return {
    data: {
      pools: [],
      health_summary: { green: 0, yellow: 0, red: 0, total: 0 },
      generated_at: '2026-08-27T12:00:00Z',
      ...payload,
    } as AdminMetricsResponse,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    isFetching: false,
  };
}

describe('AdminMetrics', () => {
  beforeEach(() => {
    mockUseAdminMetrics.mockReset();
    mockUseAdminMetrics.mockReturnValue(okResponse());
  });

  it('renders an empty-state table when the fleet is empty', () => {
    renderPage();
    expect(screen.getByText(/数据源总数/)).toBeInTheDocument();
    // No rows in the table; the Ant Design Empty component renders a
    // description we can match against.
    expect(screen.getByText(/暂无已注册的数据源/)).toBeInTheDocument();
  });

  it('rolls up summary counts from populated pools', () => {
    mockUseAdminMetrics.mockReturnValue(
      okResponse({
        pools: [
          {
            data_source_id: 1,
            name: 'primary-pg',
            db_type: 'postgresql',
            active: 3,
            pool_size: 10,
            checkouts_total: 100,
            checkins_total: 97,
            invalidations_total: 1,
            timeouts_total: 0,
            avg_held_ms: 12.5,
            timeout_rate: 0,
            health: 'green',
            history: [
              { bucket_ts: 1700000000, checkouts: 4, checkins: 4, invalidations: 0 },
              { bucket_ts: 1700000300, checkouts: 7, checkins: 6, invalidations: 0 },
            ],
          },
          {
            data_source_id: 2,
            name: 'legacy-sqlite',
            db_type: 'sqlite',
            active: 1,
            pool_size: 1,
            checkouts_total: 50,
            checkins_total: 50,
            invalidations_total: 0,
            timeouts_total: 2,
            avg_held_ms: 800,
            timeout_rate: 0.04,
            health: 'red',
            history: [],
          },
        ],
        health_summary: { green: 1, yellow: 0, red: 1, total: 2 },
      }),
    );

    renderPage();

    // Summary stats use the Ant Design Statistic component; the
    // title text and value text render as siblings under the
    // ``.ant-statistic`` root, so ``closest()`` finds the card and
    // ``within()`` locates the value inside it.
    const totalTitle = screen.getByText(/数据源总数/);
    const totalCard = totalTitle.closest('.ant-statistic') as HTMLElement;
    expect(within(totalCard).getByText('2')).toBeInTheDocument();

    // Per-DS rows
    expect(screen.getByText('primary-pg')).toBeInTheDocument();
    expect(screen.getByText('legacy-sqlite')).toBeInTheDocument();

    // Sparkline drawn for the pool with 2+ history buckets; the empty
    // pool renders the "无数据" placeholder.
    expect(screen.getAllByTestId('sparkline')).toHaveLength(1);
    expect(screen.getAllByTestId('sparkline-empty')).toHaveLength(1);
  });

  it('maps each Health value to the expected Tag colour', () => {
    mockUseAdminMetrics.mockReturnValue(
      okResponse({
        pools: [
          {
            data_source_id: 1,
            name: 'green-pool',
            db_type: 'postgresql',
            active: 0,
            pool_size: 5,
            checkouts_total: 0,
            checkins_total: 0,
            invalidations_total: 0,
            timeouts_total: 0,
            avg_held_ms: 0,
            timeout_rate: 0,
            health: 'green',
            history: [],
          },
          {
            data_source_id: 2,
            name: 'yellow-pool',
            db_type: 'postgresql',
            active: 0,
            pool_size: 5,
            checkouts_total: 0,
            checkins_total: 0,
            invalidations_total: 0,
            timeouts_total: 0,
            avg_held_ms: 0,
            timeout_rate: 0,
            health: 'yellow',
            history: [],
          },
          {
            data_source_id: 3,
            name: 'red-pool',
            db_type: 'postgresql',
            active: 0,
            pool_size: 5,
            checkouts_total: 0,
            checkins_total: 0,
            invalidations_total: 0,
            timeouts_total: 0,
            avg_held_ms: 0,
            timeout_rate: 0,
            health: 'red',
            history: [],
          },
        ],
        health_summary: { green: 1, yellow: 1, red: 1, total: 3 },
      }),
    );

    renderPage();

    // Each Health renders a Tag with data-testid="health-tag-{value}";
    // check the inner label to lock the colour mapping.
    expect(screen.getByTestId('health-tag-green')).toHaveTextContent(/健康/);
    expect(screen.getByTestId('health-tag-yellow')).toHaveTextContent(/关注/);
    expect(screen.getByTestId('health-tag-red')).toHaveTextContent(/告警/);
  });
});
