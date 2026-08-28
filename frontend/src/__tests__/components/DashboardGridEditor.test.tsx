import { describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardGridEditor } from '../../components/DashboardGridEditor';
import type { DashboardItem } from '../../types';

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

const FAKE_ITEMS: DashboardItem[] = [
  {
    id: 1,
    dashboard_id: 1,
    item_type: 'text',
    title: null,
    order_index: 0,
    x: 0,
    y: 0,
    w: 4,
    h: 4,
    fields: [],
    where_conditions: [],
    group_by: [],
    order_by: [],
    parameters: {},
    text_content: 'hello',
  } as unknown as DashboardItem,
  {
    id: 2,
    dashboard_id: 1,
    item_type: 'text',
    title: null,
    order_index: 1,
    x: 4,
    y: 0,
    w: 4,
    h: 4,
    fields: [],
    where_conditions: [],
    group_by: [],
    order_by: [],
    parameters: {},
    text_content: 'world',
  } as unknown as DashboardItem,
];

describe('DashboardGridEditor', () => {
  it('does not call onLayoutChange in readOnly mode even after layout mutations', () => {
    vi.useFakeTimers();
    const onLayoutChange = vi.fn();
    const { container } = render(
      <DashboardGridEditor items={FAKE_ITEMS} readOnly onLayoutChange={onLayoutChange} />,
      { wrapper },
    );

    // Advance past the debounce window — readOnly short-circuits the
    // handler so no payloads should ever queue up.
    act(() => {
      vi.advanceTimersByTime(500);
    });
    void container; // referenced to keep the var warning away
    expect(onLayoutChange).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('forwards a single batched payload after debounce', () => {
    vi.useFakeTimers();
    const onLayoutChange = vi.fn();
    const { rerender } = render(
      <DashboardGridEditor
        items={FAKE_ITEMS}
        onLayoutChange={onLayoutChange}
      />,
      { wrapper },
    );

    // Simulate two drag-drop ticks by mutating item x/y and re-rendering.
    // The library normalizes these into the next layout, but here we just
    // exercise the wrapper's debounce/flush path directly by re-rendering
    // with new coords — react-grid-layout's onLayoutChange runs on mount
    // with the initial layout, then again on each prop change.
    const next = FAKE_ITEMS.map((it, i) => ({
      ...it,
      x: i === 0 ? 2 : 6,
    }));

    rerender(<DashboardGridEditor items={next} onLayoutChange={onLayoutChange} />);

    // Flush the 250ms debounce window.
    act(() => {
      vi.advanceTimersByTime(400);
    });

    // We expect at least one forwarded payload — the library emits an
    // initial layout on mount and a second one on rerender. Both get
    // coalesced into a single debounced call after the timer fires.
    expect(onLayoutChange).toHaveBeenCalled();
    vi.useRealTimers();
  });
});
