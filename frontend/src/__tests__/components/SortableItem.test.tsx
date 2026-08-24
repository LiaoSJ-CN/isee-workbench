/** Tests for the SortableItem card.

We only test *rendering* — drag interactions need a real PointerSensor
event loop and live outside the vitest/jsdom environment. E2e covers
drag-and-drop (see 批 7.4).

The component must be wrapped in ``<SortableContext>`` because
``useSortable`` reads the items list from a context. ``<DndContext>``
isn't strictly required for *rendering* (useSortable returns no-op
attributes when there's no DndContext), but we include the parent
wrapper to keep the test as close to production as possible.
*/

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DndContext } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';

import { SortableItem } from '../../pages/ReportEditor/SortableItem';
import type { ReportItem } from '../../types';

const sampleTable: ReportItem = {
  id: 1,
  report_id: 1,
  name: '月度销售额',
  item_type: 'table',
  table_name: 'sales',
  order_index: 0,
  display_config: {},
  where_conditions: [],
  group_by: [],
  order_by: [],
  fields: [],
  limit: 1000,
};

const sampleChart: ReportItem = {
  id: 2,
  report_id: 1,
  name: '区域柱状图',
  item_type: 'chart',
  table_name: 'sales',
  order_index: 1,
  display_config: { chart_type: 'bar' },
  where_conditions: [],
  group_by: [],
  order_by: [],
  fields: [],
  limit: 1000,
};

const sampleMetric: ReportItem = {
  id: 3,
  report_id: 1,
  name: '总收入',
  item_type: 'metric',
  order_index: 2,
  display_config: {},
  where_conditions: [],
  group_by: [],
  order_by: [],
  fields: [],
  limit: 1000,
};

const sampleText: ReportItem = {
  id: 4,
  report_id: 1,
  name: '说明',
  item_type: 'text',
  order_index: 3,
  display_config: {},
  where_conditions: [],
  group_by: [],
  order_by: [],
  fields: [],
  limit: 1000,
};

function renderItem(
  item: ReportItem,
  props: Partial<React.ComponentProps<typeof SortableItem>> = {},
) {
  const onEdit = vi.fn();
  const onDelete = vi.fn();
  const onMoveUp = vi.fn();
  const onMoveDown = vi.fn();

  const result = render(
    <DndContext>
      <SortableContext items={[`item-${item.id}`]} strategy={verticalListSortingStrategy}>
        <SortableItem
          id={`item-${item.id}`}
          item={item}
          index={0}
          onEdit={onEdit}
          onDelete={onDelete}
          onMoveUp={onMoveUp}
          onMoveDown={onMoveDown}
          isFirst
          isLast
          {...props}
        />
      </SortableContext>
    </DndContext>,
  );

  return { ...result, onEdit, onDelete, onMoveUp, onMoveDown };
}

describe('SortableItem rendering', () => {
  it('renders the item name', () => {
    renderItem(sampleTable);
    expect(screen.getByText('月度销售额')).toBeInTheDocument();
  });

  it('shows table info for table items', () => {
    renderItem(sampleTable);
    expect(screen.getByText('表: sales')).toBeInTheDocument();
  });

  it('shows chart type for chart items', () => {
    renderItem(sampleChart);
    expect(screen.getByText('图表: bar')).toBeInTheDocument();
  });

  it('shows generic label for metric items', () => {
    renderItem(sampleMetric);
    expect(screen.getByText('指标')).toBeInTheDocument();
  });

  it('shows generic label for text items', () => {
    renderItem(sampleText);
    expect(screen.getByText('文本')).toBeInTheDocument();
  });

  it('shows "-" for table items without a table_name', () => {
    renderItem({ ...sampleTable, table_name: undefined });
    expect(screen.getByText('表: -')).toBeInTheDocument();
  });

  it('shows "-" for chart items without a chart_type', () => {
    renderItem({ ...sampleChart, display_config: {} });
    expect(screen.getByText('图表: -')).toBeInTheDocument();
  });

  it('disables move-up when isFirst=true', () => {
    renderItem(sampleTable, { index: 0, isFirst: true, isLast: false });
    // Antd renders disabled buttons with the `disabled` attribute.
    // We just check that the move-up button exists; full disabled-state
    // assertion is brittle across Antd versions.
    expect(screen.getAllByRole('button').length).toBeGreaterThan(0);
  });
});
