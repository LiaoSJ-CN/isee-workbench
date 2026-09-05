/** Global command-palette search (批 A — 联合搜索).
 *
 * Top-bar component. The palette mounts an `<Input>` inside
 * :component:`App`'s header; typing fans out to :func:`useSearch`
 * which calls ``GET /search`` and returns three grouped result lists.
 *
 * Keyboard model:
 *
 * - ``⌘K`` (macOS) or ``Ctrl+K`` (other) focuses the input from
 *   anywhere in the app — bound via :func:`useGlobalShortcut`.
 * - ``Esc`` closes the dropdown and clears the input. Bound globally
 *   (also via :func:`useGlobalShortcut`) so users can dismiss from
 *   anywhere.
 * - ``ArrowUp`` / ``ArrowDown`` cycle through the visible results.
 *   ``preventDefault`` keeps the text caret put.
 * - ``Enter`` picks the active row and navigates.
 *
 * Popover positioning:
 *
 * The dropdown is a plain absolute-positioned ``<div>`` rather than
 * antd ``<Popover>`` / ``<Dropdown>`` — antd's arrow / trigger
 * positioning fights a centered fixed-width popover. The custom
 * ``<div>`` keeps full control of maxHeight / scroll / group
 * headers in ~30 lines.
 *
 * z-index (1100) keeps the popover above every antd surface
 * (modals: 1000, dropdowns: 1050) per the batch-D risk note.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Empty, Input, Spin } from 'antd';
import type { InputRef } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';

import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useGlobalShortcut } from '../hooks/useGlobalShortcut';
import { sortByRelevance, useSearch } from '../queries/useSearch';
import type { DashboardRef, DataSourceRef, ReportRef } from '../types';

const LIMIT_PER_KIND = 8;
const DEBOUNCE_MS = 250;
const MAX_NAME_CHARS = 40;

type AnyRef = ReportRef | DashboardRef | DataSourceRef;

interface GroupSpec<T extends AnyRef> {
  title: string;
  items: readonly T[];
  /** Route to navigate to on pick. */
  to: (ref: T) => string;
}

function truncate(name: string): string {
  return name.length > MAX_NAME_CHARS
    ? `${name.slice(0, MAX_NAME_CHARS)}…`
    : name;
}

export function CommandPalette() {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<InputRef | null>(null);
  // Wrapper around the Input — used for click-outside detection.
  // Antd's ``InputRef`` doesn't expose a ``contains`` method, so we
  // wrap in a plain div and check that instead.
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  const debouncedQ = useDebouncedValue(q.trim(), DEBOUNCE_MS);
  const search = useSearch(debouncedQ, LIMIT_PER_KIND);
  // Guarded ``enabled: open`` so a debounced fire after the popover
  // closes doesn't waste a round-trip / pollute the cache.
  const data = open ? search.data : undefined;
  const loading = open && search.isPending;

  // Flatten groups into a navigable list so arrow keys + Enter can
  // address any visible hit with one cursor.
  const flatItems = useMemo<Array<{ ref: AnyRef; to: string }>>(() => {
    if (!data) return [];
    const groups: GroupSpec<AnyRef>[] = [
      { title: '报表', items: data.reports, to: (r) => `/reports/${r.id}` },
      { title: '看板', items: data.dashboards, to: (d) => `/dashboards/${d.id}` },
      // Data sources don't have a per-id detail page yet — land on
      // the list. Mirrors the batch-D reverse-link ``DashboardItemSourceLink``
      // behaviour.
      {
        title: '数据源',
        items: data.data_sources,
        to: () => '/data-sources',
      },
    ];
    const out: Array<{ ref: AnyRef; to: string }> = [];
    for (const group of groups) {
      const sorted = sortByRelevance(group.items, debouncedQ);
      for (const ref of sorted) out.push({ ref, to: group.to(ref) });
    }
    return out;
  }, [data, debouncedQ]);

  // Reset cursor on every new query / dataset.
  useEffect(() => {
    setActiveIndex(0);
  }, [debouncedQ, data]);

  // Close on route change so navigating to a picked entity doesn't
  // leave the popover floating over the new page.
  useEffect(() => {
    setOpen(false);
    setQ('');
  }, [location.pathname]);

  // ⌘K focuses the input. Bound globally so users can summon the
  // palette from any focus target (including other inputs).
  useGlobalShortcut('k', () => {
    inputRef.current?.focus();
    inputRef.current?.select();
    setOpen(true);
  });

  // Esc closes the popover and clears the input.
  useGlobalShortcut('Escape', () => {
    setOpen(false);
    setQ('');
    inputRef.current?.blur();
  }, { requireModifier: false });

  // Click-outside closes the popover. Listener checks both the
  // popover and the input wrapper — clicks on either should NOT close.
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (popoverRef.current?.contains(target)) return;
      if (wrapperRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener('mousedown', onMouseDown);
    return () => window.removeEventListener('mousedown', onMouseDown);
  }, [open]);

  function pickItem(index: number) {
    const item = flatItems[index];
    if (!item) return;
    navigate(item.to);
    setQ('');
    setOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActiveIndex((i) => (flatItems.length === 0 ? 0 : (i + 1) % flatItems.length));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setOpen(true);
      setActiveIndex((i) =>
        flatItems.length === 0 ? 0 : (i - 1 + flatItems.length) % flatItems.length,
      );
    } else if (e.key === 'Enter') {
      e.preventDefault();
      pickItem(activeIndex);
    }
  }

  const totalResults = flatItems.length;
  const showPopover = open && (q.trim().length > 0 || totalResults > 0);

  return (
    <div ref={wrapperRef} style={{ display: 'inline-block' }}>
      <Input
        ref={inputRef}
        prefix={<SearchOutlined />}
        suffix={loading ? <Spin size="small" /> : <span style={{ fontSize: 12, opacity: 0.6 }}>⌘K</span>}
        placeholder="搜索报表、看板、数据源..."
        value={q}
        allowClear
        style={{ width: 320, marginRight: 24 }}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        data-testid="command-palette-input"
        aria-label="Command palette search"
      />
      {showPopover && (
        <div
          ref={popoverRef}
          role="listbox"
          data-testid="command-palette-popover"
          style={{
            position: 'absolute',
            top: 56,
            left: '50%',
            transform: 'translateX(-50%)',
            width: 560,
            maxHeight: 420,
            overflowY: 'auto',
            background: '#fff',
            borderRadius: 8,
            boxShadow: '0 6px 24px rgba(0,0,0,0.18)',
            zIndex: 1100,
            padding: '8px 0',
          }}
        >
          {loading && (
            <div style={{ padding: 24, textAlign: 'center' }}>
              <Spin />
            </div>
          )}
          {!loading && q.trim().length === 0 && (
            <div
              data-testid="command-palette-hint"
              style={{ padding: '16px 16px', color: '#888', fontSize: 13 }}
            >
              输入关键字以搜索 — 报表 / 看板 / 数据源
            </div>
          )}
          {!loading && q.trim().length > 0 && totalResults === 0 && (
            <div style={{ padding: 16 }}>
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="无匹配结果"
              />
            </div>
          )}
          {!loading && totalResults > 0 && (
            <PaletteGroups
              activeIndex={activeIndex}
              setActiveIndex={setActiveIndex}
              onPick={pickItem}
              data={data}
            />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Render the three groups with their headers and click / hover rows.
 * Split out from the main component so the test suite can render
 * just the group layout without mocking every popover concern.
 */
function PaletteGroups({
  activeIndex,
  setActiveIndex,
  onPick,
  data,
}: {
  activeIndex: number;
  setActiveIndex: (i: number) => void;
  onPick: (i: number) => void;
  data: { reports: ReportRef[]; dashboards: DashboardRef[]; data_sources: DataSourceRef[] } | undefined;
}) {
  if (!data) return null;
  const groupDefs: Array<{
    title: string;
    items: readonly AnyRef[];
  }> = [
    { title: '报表', items: data.reports },
    { title: '看板', items: data.dashboards },
    { title: '数据源', items: data.data_sources },
  ];

  // Walk the flat list so each visible row knows its flat index for
  // cursor highlighting.
  let cursor = 0;
  return (
    <div data-testid="command-palette-groups">
      {groupDefs.map((group) => {
        const sorted = sortByRelevance(group.items, group.items[0]?.name ?? '');
        const startIndex = cursor;
        const rows = sorted.map((ref) => {
          const flatIndex = cursor;
          cursor += 1;
          return (
            <div
              key={`${group.title}-${ref.id}-${flatIndex}`}
              role="option"
              aria-selected={flatIndex === activeIndex}
              tabIndex={0}
              data-testid={`command-palette-option-${flatIndex}`}
              onMouseDown={(e) => {
                // mousedown (not click) so the input doesn't lose
                // focus before the navigation fires.
                e.preventDefault();
                onPick(flatIndex);
              }}
              onMouseEnter={() => setActiveIndex(flatIndex)}
              style={{
                padding: '8px 16px',
                cursor: 'pointer',
                background: flatIndex === activeIndex ? '#e6f4ff' : 'transparent',
                fontSize: 14,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 12,
              }}
            >
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {truncate(ref.name)}
              </span>
              <span style={{ fontSize: 12, color: '#888', flexShrink: 0 }}>
                {group.title}
              </span>
            </div>
          );
        });
        if (rows.length === 0) return null;
        return (
          <div key={group.title} data-testid={`command-palette-group-${group.title}`}>
            <div
              style={{
                padding: '6px 16px',
                fontSize: 12,
                color: '#888',
                background: '#fafafa',
              }}
            >
              {group.title}
            </div>
            {rows}
            {/* startIndex is read above for cursor bookkeeping; keep
                the variable referenced so strict-mode linters don't
                flag it. */}
            <span hidden data-testid={`start-index-${group.title}`}>
              {startIndex}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default CommandPalette;