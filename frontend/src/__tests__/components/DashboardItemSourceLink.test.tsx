import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';

import { DashboardItemSourceLink } from '../../components/DashboardItemSourceLink';
import type { DashboardItem } from '../../types';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ConfigProvider>{children}</ConfigProvider>
);

function makeItem(overrides: Partial<DashboardItem>): DashboardItem {
  return {
    id: 100,
    dashboard_id: 1,
    item_type: 'report',
    title: 'Some item',
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
    ...overrides,
  } as DashboardItem;
}

describe('DashboardItemSourceLink (D 双向 link)', () => {
  it('renders nothing for text items', () => {
    const onOpen = vi.fn();
    const { container } = render(
      <DashboardItemSourceLink item={makeItem({ item_type: 'text' })} onOpen={onOpen} />,
      { wrapper },
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when a report item has no report_id', () => {
    const onOpen = vi.fn();
    const { container } = render(
      <DashboardItemSourceLink
        item={makeItem({ item_type: 'report', report_id: undefined })}
        onOpen={onOpen}
      />,
      { wrapper },
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when a chart item has no data_source_id', () => {
    const onOpen = vi.fn();
    const { container } = render(
      <DashboardItemSourceLink
        item={makeItem({ item_type: 'chart', data_source_id: undefined })}
        onOpen={onOpen}
      />,
      { wrapper },
    );
    expect(container.firstChild).toBeNull();
  });

  it('fires onOpen with the item when clicked on a report card', () => {
    const onOpen = vi.fn();
    const item = makeItem({ item_type: 'report', report_id: 42 });
    render(<DashboardItemSourceLink item={item} onOpen={onOpen} />, { wrapper });
    const btn = screen.getByTestId('dashboard-item-source-link');
    fireEvent.click(btn);
    expect(onOpen).toHaveBeenCalledWith(item);
  });

  it('fires onOpen with the item when clicked on a chart card', () => {
    const onOpen = vi.fn();
    const item = makeItem({ item_type: 'chart', data_source_id: 7 });
    render(<DashboardItemSourceLink item={item} onOpen={onOpen} />, { wrapper });
    fireEvent.click(screen.getByTestId('dashboard-item-source-link'));
    expect(onOpen).toHaveBeenCalledWith(item);
  });

  it('stops click propagation so the parent card handler does not fire', () => {
    const onOpen = vi.fn();
    const onCardClick = vi.fn();
    const item = makeItem({ item_type: 'report', report_id: 42 });
    render(
      // Wrap in a parent that records bubbled clicks; the inner
      // button click should NOT bubble up.
      <div onClick={onCardClick}>
        <DashboardItemSourceLink item={item} onOpen={onOpen} />
      </div>,
      { wrapper },
    );
    fireEvent.click(screen.getByTestId('dashboard-item-source-link'));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('exposes an accessible label via aria-label', () => {
    const item = makeItem({ item_type: 'report', report_id: 1 });
    render(<DashboardItemSourceLink item={item} onOpen={vi.fn()} />, { wrapper });
    expect(screen.getByLabelText('打开引用的报表')).toBeInTheDocument();
  });

  it('renders a different tooltip for chart items', () => {
    const item = makeItem({ item_type: 'chart', data_source_id: 1 });
    render(<DashboardItemSourceLink item={item} onOpen={vi.fn()} />, { wrapper });
    expect(screen.getByLabelText('打开引用的数据源')).toBeInTheDocument();
  });
});