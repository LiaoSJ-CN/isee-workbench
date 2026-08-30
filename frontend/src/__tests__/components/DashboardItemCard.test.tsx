import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { DashboardItemCard } from '../../components/DashboardItemCard';
import { dashboardApi } from '../../api';
import type { DashboardItem } from '../../types';

const TEXT_ITEM = {
  id: 10,
  dashboard_id: 1,
  item_type: 'text',
  title: 'Note',
  order_index: 0,
  x: 0,
  y: 0,
  w: 12,
  h: 1,
  fields: [],
  where_conditions: [],
  group_by: [],
  order_by: [],
  parameters: {},
  text_content: 'inline text body',
} as unknown as DashboardItem;

const REPORT_ITEM = {
  id: 11,
  dashboard_id: 1,
  item_type: 'report',
  title: 'Embedded report',
  order_index: 1,
  x: 0,
  y: 1,
  w: 6,
  h: 4,
  report_id: 42,
  fields: [],
  where_conditions: [],
  group_by: [],
  order_by: [],
  parameters: {},
} as unknown as DashboardItem;

const CHART_ITEM = {
  id: 12,
  dashboard_id: 1,
  item_type: 'chart',
  title: 'Trend',
  order_index: 2,
  x: 6,
  y: 1,
  w: 6,
  h: 4,
  data_source_id: 1,
  table_name: 't',
  fields: [],
  where_conditions: [],
  group_by: [],
  order_by: [],
  parameters: {},
  display_config: { chart_type: 'line' },
} as unknown as DashboardItem;

afterEach(() => {
  vi.restoreAllMocks();
});

describe('DashboardItemCard', () => {
  beforeEach(() => {
    // jsdom doesn't implement URL.createObjectURL; provide a stub so
    // the IframeBody branch can mount without throwing.
    if (!('createObjectURL' in URL.prototype)) {
      URL.createObjectURL = vi.fn(() => 'blob:fake-url');
      URL.revokeObjectURL = vi.fn();
    }
  });

  it('renders text inline without invoking the API', () => {
    const spy = vi.spyOn(dashboardApi, 'previewItem');
    const { container } = render(<DashboardItemCard item={TEXT_ITEM} />);
    expect(container.textContent).toContain('inline text body');
    expect(spy).not.toHaveBeenCalled();
  });

  it('fetches preview HTML for chart items and embeds it in an iframe', async () => {
    const spy = vi
      .spyOn(dashboardApi, 'previewItem')
      .mockResolvedValue('<canvas></canvas>');
    const { container } = render(<DashboardItemCard item={CHART_ITEM} />);
    expect(spy).toHaveBeenCalledWith(1, 12);
    await waitFor(() => {
      const iframe = container.querySelector('iframe');
      expect(iframe).not.toBeNull();
      // blob URL (or fallback) lands on src; the loading spinner
      // disappears once URL.createObjectURL has resolved.
      expect(iframe?.getAttribute('src')).toBe('blob:fake-url');
    });
  });

  it('fetches preview HTML for report items (same endpoint as chart)', async () => {
    const spy = vi
      .spyOn(dashboardApi, 'previewItem')
      .mockResolvedValue('<div>report</div>');
    const { container } = render(<DashboardItemCard item={REPORT_ITEM} />);
    expect(spy).toHaveBeenCalledWith(1, 11);
    await waitFor(() => {
      const iframe = container.querySelector('iframe');
      expect(iframe?.getAttribute('src')).toBe('blob:fake-url');
    });
  });

  it('surfaces an error Alert when the API rejects', async () => {
    vi.spyOn(dashboardApi, 'previewItem').mockRejectedValue(
      new Error('boom'),
    );
    const { container } = render(<DashboardItemCard item={CHART_ITEM} />);
    await waitFor(() => {
      // antd Alert renders the message inside a ``.ant-alert-message``
      // span; we don't pin the exact className, just the text.
      expect(container.textContent).toContain('boom');
    });
  });
});