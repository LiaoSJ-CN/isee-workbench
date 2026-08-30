/** Drag-resize grid for dashboard items (批 14.3).
 *
 * Wraps react-grid-layout's :component:`Responsive` + :component:`WidthProvider`
 * to give us a 12-col grid whose cells stretch to the parent width. Layout
 * deltas are batched into a single ``onLayoutChange`` payload so we can
 * debounce them into one ``PATCH /dashboards/{id}/layout`` round-trip rather
 * than spamming the API on every pixel of drag.
 *
 * Two ACL-related caveats worth pinning:
 * - The render prop renders ``DashboardItemCard`` for every cell, but the
 *   card itself is **read-only** — the surrounding :component:`DashboardEdit`
 *   page decides what to do on click (open item editor). The grid only owns
 *   position + size.
 * - react-grid-layout ships its own CSS in ``react-grid-layout/css/styles.css``
 *   and ``react-resizable/css/styles.css``. We import them once here so this
 *   component is the single source of truth for those styles.
 */

import { useEffect, useMemo, useRef } from 'react';
import { Popconfirm, Button } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';
import { GridLayout, useContainerWidth, type Layout } from 'react-grid-layout';

import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

import { DashboardItemCard } from './DashboardItemCard';
import type { DashboardItem } from '../types';

export interface DashboardGridEditorProps {
  items: DashboardItem[];
  /** Stable per-item key. Falls back to ``id``; pass something custom only
   *  when you want to force a remount across structural changes. */
  itemKey?: (item: DashboardItem) => string;
  /** Called whenever the user drops a drag/resize. We debounce before
   *  forwarding so a 1-second drag becomes one PATCH, not 60.
   *  Note the field name ``item_id`` — the backend's
   *  ``DashboardItemLayoutEntry`` uses ``item_id`` (matching the DB
   *  foreign-key column), not ``id``. */
  onLayoutChange: (
    entries: { item_id: number; x: number; y: number; w: number; h: number }[],
  ) => void;
  /** Optional click-to-edit hook. */
  onItemClick?: (item: DashboardItem) => void;
  /** Optional per-item delete hook. Renders a small "×" affordance in
   *  the top-right corner of every cell in edit mode. */
  onItemDelete?: (item: DashboardItem) => void;
  /** Id of the item currently being deleted (loading state). */
  deletingItemId?: number | null;
  /** Read-only mode (hide drag/resize handles, swallow click). */
  readOnly?: boolean;
  /** Debounce ms for forwarding layout changes upstream. */
  debounceMs?: number;
}

const COLS = 12;
const ROW_HEIGHT = 60;
const GUTTER: [number, number] = [12, 12];

export function DashboardGridEditor({
  items,
  itemKey,
  onLayoutChange,
  onItemClick,
  onItemDelete,
  deletingItemId,
  readOnly = false,
  debounceMs = 250,
}: DashboardGridEditorProps) {
  // Generate the react-grid-layout ``Layout`` array. The library keys by
  // whatever we put in ``i``, so we use the item id (stringified).
  // Note: react-grid-layout v2's ``Layout`` type is itself ``readonly
  // LayoutItem[]`` — the prop and onLayoutChange callback both want
  // ``Layout`` directly, not ``Layout[]``.
  const layout: Layout = useMemo(
    () =>
      items.map((it) => ({
        i: String(it.id),
        x: it.x,
        y: it.y,
        w: it.w,
        h: it.h,
        minW: 2,
        minH: 2,
      })),
    [items],
  );

  // react-grid-layout v2 dropped the WidthProvider HOC; we observe
  // container width ourselves and gate render on the first measurement
  // so cells don't flicker with the initial 1280 fallback.
  const { width, containerRef, mounted } = useContainerWidth({ measureBeforeMount: false });

  // Debounce layout change forward to parent. The library calls onLayoutChange
  // on EVERY onDrag/onResize tick — without debouncing we'd send a PATCH per
  // pixel. We keep a single timer ref so the most recent call wins.
  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef<
    { item_id: number; x: number; y: number; w: number; h: number }[]
  >([]);
  const flushRef = useRef(onLayoutChange);
  // Keep the latest callback ref updated after every render — assigning in
  // an effect (rather than during render) avoids the
  // "Cannot update ref during render" lint rule.
  useEffect(() => {
    flushRef.current = onLayoutChange;
  });

  useEffect(() => {
    return () => {
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, []);

  const handleLayoutChange = (next: Layout) => {
    if (readOnly) return;
    const flat = next.flat();
    // Replace pending entries keyed by item_id; preserve last write per id.
    const map = new Map<
      number,
      { item_id: number; x: number; y: number; w: number; h: number }
    >();
    for (const entry of pendingRef.current) map.set(entry.item_id, entry);
    for (const entry of flat) {
      const id = Number(entry.i);
      if (!Number.isFinite(id)) continue;
      map.set(id, { item_id: id, x: entry.x, y: entry.y, w: entry.w, h: entry.h });
    }
    pendingRef.current = Array.from(map.values());

    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      const payload = pendingRef.current;
      pendingRef.current = [];
      timerRef.current = null;
      flushRef.current(payload);
    }, debounceMs);
  };

  // For each item id in ``items`` we render the read-only card; the grid lib
  // wraps it in a draggable + resizable container.
  const renderKey = itemKey ?? ((it: DashboardItem) => String(it.id));

  return (
    <div ref={containerRef}>
      {mounted && (
        <GridLayout
          className="dashboard-grid"
          layout={layout}
          width={width}
          // react-grid-layout v2 groups cols/rowHeight/margin/containerPadding
          // into a single ``gridConfig`` partial. Use the readOnly flag to
          // gate both drag + resize at the config layer.
          gridConfig={{
            cols: COLS,
            rowHeight: ROW_HEIGHT,
            margin: GUTTER,
            containerPadding: [0, 0] as const,
            maxRows: Infinity,
          }}
          dragConfig={{
            enabled: !readOnly,
            handle: '.dashboard-item-drag-handle',
          }}
          resizeConfig={{
            enabled: !readOnly,
          }}
          onLayoutChange={handleLayoutChange}
          // z-index risk: react-grid-layout sets zIndex: 100 on drag. AntD Modal
          // default zIndex is 1000 so modal still wins, but if you stack a Modal
          // inside a draggable cell it can flicker. The parent page sets a lower
          // zIndex on the grid container for safety (see DashboardEdit).
          style={{ position: 'relative' }}
        >
          {items.map((item) => (
            <div
              key={renderKey(item)}
              data-grid-id={item.id}
              role={onItemClick && !readOnly ? 'button' : undefined}
              tabIndex={onItemClick && !readOnly ? 0 : undefined}
              onClick={() => {
                if (!readOnly && onItemClick) onItemClick(item);
              }}
              onKeyDown={(e) => {
                if (readOnly || !onItemClick) return;
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onItemClick(item);
                }
              }}
              style={{
                cursor: readOnly ? 'default' : onItemClick ? 'pointer' : 'move',
                position: 'relative',
              }}
            >
              {!readOnly && onItemDelete && (
                <Popconfirm
                  title="确认删除该看板项？"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={(e) => {
                    e?.stopPropagation();
                    onItemDelete(item);
                  }}
                  onCancel={(e) => e?.stopPropagation()}
                >
                  <Button
                    size="small"
                    danger
                    type="primary"
                    icon={<DeleteOutlined />}
                    loading={deletingItemId === item.id}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      position: 'absolute',
                      top: 4,
                      right: 4,
                      zIndex: 5,
                      opacity: 0.85,
                    }}
                  />
                </Popconfirm>
              )}
              <DashboardItemCard item={item} />
            </div>
          ))}
        </GridLayout>
      )}
    </div>
  );
}
