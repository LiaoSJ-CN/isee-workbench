/** Tests for the template marketplace gallery (批 13).
 *
 * Mirrors ``AuditLog.test.tsx`` + ``AdminMetrics.test.tsx`` — we mock
 * the React Query hooks so the test exercises only the render path
 * and the filter → query-key wiring. No network, no router, no
 * full app shell.
 *
 * Coverage matrix (mirrors the plan's #5 scope):
 *
 * 1. Empty fleet renders the friendly empty state.
 * 2. Populated fleet renders one card per template with the right
 *    name / data source / item-count / visibility tag.
 * 3. Typing in the search input pushes ``q`` through to the
 *    ``useReportTemplates`` filter object.
 * 4. Clicking "使用此模板" triggers ``useForkReport`` with the
 *    template id, and the page navigates to the new report's
 *    editor on success.
 *
 * Note: the page also depends on ``useDataSources`` (for the data-
 * source Select + per-card DS name lookup); we mock it with an
 * empty array so the Select is empty and cards fall back to the
 * ``ID: N`` placeholder. None of the assertions below depend on
 * that fallback.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockUseReportTemplates = vi.fn();
const mockUseDataSources = vi.fn();
const mockUseForkReport = vi.fn();

vi.mock('../../queries/useReportTemplates', () => ({
  useReportTemplates: (filters?: unknown) => mockUseReportTemplates(filters),
  useForkReport: () => mockUseForkReport(),
}));

vi.mock('../../queries/useDataSources', () => ({
  useDataSources: () => mockUseDataSources(),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

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

import ReportTemplates from '../../pages/ReportTemplates';
import type { Report } from '../../types';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReportTemplates />
    </QueryClientProvider>,
  );
}

function emptyResponse() {
  return {
    data: [] as Report[],
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

const SAMPLE_TEMPLATES: Report[] = [
  {
    id: 101,
    name: '月度销售看板',
    description: '滚动 12 个月销售额 + 同比',
    data_source_id: 1,
    template_category: '销售分析',
    template_source_id: null,
    visibility: 'public',
    is_template: true,
    is_active: true,
    is_scheduled: false,
    output_formats: ['excel', 'html'],
    items: [
      {
        id: 1,
        report_id: 101,
        name: '销售额',
        item_type: 'chart',
        order_index: 0,
        fields: [],
        where_conditions: [],
        group_by: [],
        order_by: [],
      },
      {
        id: 2,
        report_id: 101,
        name: '明细表',
        item_type: 'table',
        order_index: 1,
        fields: [],
        where_conditions: [],
        group_by: [],
        order_by: [],
      },
    ],
  },
  {
    id: 102,
    name: '应收账龄（部门）',
    description: '按客户经理分组',
    data_source_id: 2,
    template_category: '财务分析',
    template_source_id: null,
    visibility: 'org',
    is_template: true,
    is_active: true,
    is_scheduled: false,
    output_formats: ['excel'],
    items: [
      {
        id: 3,
        report_id: 102,
        name: '账龄',
        item_type: 'chart',
        order_index: 0,
        fields: [],
        where_conditions: [],
        group_by: [],
        order_by: [],
      },
    ],
  },
];

function forkMockReturn() {
  // ``useForkReport`` returns a useMutation result shape; only
  // ``mutate`` / ``isPending`` / ``variables`` are touched by the
  // component, so the rest is stubbed.
  return {
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
    onSuccess: vi.fn(),
    onError: vi.fn(),
    data: undefined,
    error: null,
    reset: vi.fn(),
  };
}

describe('ReportTemplates page', () => {
  beforeEach(() => {
    mockUseReportTemplates.mockReset();
    mockUseDataSources.mockReset();
    mockUseForkReport.mockReset();
    mockNavigate.mockReset();
    mockUseReportTemplates.mockReturnValue(emptyResponse());
    mockUseDataSources.mockReturnValue({ data: [] as never[], isPending: false });
    mockUseForkReport.mockReturnValue(forkMockReturn());
  });

  it('renders the empty state when no templates are visible', () => {
    renderPage();
    // The empty state lives on a Card with a data-testid so the
    // test can locate it without depending on Ant Design's
    // internal class names.
    expect(screen.getByTestId('empty-state')).toBeInTheDocument();
    expect(screen.getByText(/暂无可用模板/)).toBeInTheDocument();
  });

  it('renders one card per template with the expected fields', () => {
    mockUseReportTemplates.mockReturnValue({
      ...emptyResponse(),
      data: SAMPLE_TEMPLATES,
    });
    mockUseDataSources.mockReturnValue({
      data: [
        { id: 1, name: 'primary-pg', db_type: 'postgresql' },
        { id: 2, name: 'erp-demo', db_type: 'sqlite' },
      ] as never[],
      isPending: false,
    });

    renderPage();

    const cards = screen.getAllByTestId('template-card');
    expect(cards).toHaveLength(2);

    // First card: name + category + visibility tag + item count.
    expect(within(cards[0]).getByText('月度销售看板')).toBeInTheDocument();
    expect(within(cards[0]).getByText('销售分析')).toBeInTheDocument();
    expect(within(cards[0]).getByText(/报表项/)).toHaveTextContent('2');

    // Second card: org visibility + 1 item + different DS.
    expect(within(cards[1]).getByText('应收账龄（部门）')).toBeInTheDocument();
    expect(within(cards[1]).getByText('同部门')).toBeInTheDocument();
    expect(within(cards[1]).getByText(/报表项/)).toHaveTextContent('1');

    // Data source name rendered via the mock list (instead of the
    // ``ID: N`` fallback). Antd Typography renders the "数据源：" prefix
    // and the name as separate <Text> spans under a parent <Space>,
    // so we walk the rendered text via a regex on the card's
    // textContent (cheaper than fighting RTL's element-boundary
    // matcher).
    expect(cards[0].textContent).toMatch(/数据源：.*primary-pg/);
    expect(cards[1].textContent).toMatch(/数据源：.*erp-demo/);
  });

  it('sends the typed search through to useReportTemplates as q', async () => {
    const user = userEvent.setup();
    renderPage();

    const input = screen.getByTestId('q-input');
    await user.type(input, '销售');

    await waitFor(() => {
      const lastCall = mockUseReportTemplates.mock.calls.at(-1)?.[0] as
        Record<string, unknown> | undefined;
      expect(lastCall).toBeDefined();
      expect(lastCall).toHaveProperty('q', '销售');
    });
  });

  it('forks on "使用此模板" click and navigates to the new report', async () => {
    const user = userEvent.setup();
    mockUseReportTemplates.mockReturnValue({
      ...emptyResponse(),
      data: SAMPLE_TEMPLATES,
    });

    // Capture the mutate fn so we can assert onSuccess fires with
    // the new report id — that's how the page navigates.
    const mutate = vi.fn((_vars, opts) => {
      // Simulate the success callback the React Query mutation
      // would invoke with the freshly-forked row.
      opts?.onSuccess?.({ id: 999, name: '我的销售看板' } as Report);
    });
    mockUseForkReport.mockReturnValue({ ...forkMockReturn(), mutate });

    renderPage();

    const cards = screen.getAllByTestId('template-card');
    await user.click(within(cards[0]).getByRole('button', { name: /使用此模板/ }));

    await waitFor(() => {
      expect(mutate).toHaveBeenCalledWith(
        expect.objectContaining({ templateId: 101 }),
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
      // The success handler routes into /reports/{id}; assert the
      // id matches the one we returned from the simulated mutate.
      expect(mockNavigate).toHaveBeenCalledWith('/reports/999');
    });
  });
});
