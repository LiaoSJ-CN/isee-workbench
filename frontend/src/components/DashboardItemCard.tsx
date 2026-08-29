/** Read-only renderer for a single Dashboard item (批 14 / 14.7).
 *
 * Three flavours:
 * - ``report`` — fetches the item's standalone HTML preview via
 *   ``GET /dashboards/{id}/items/{item_id}/preview`` and embeds it
 *   in a sandboxed iframe.
 * - ``chart`` — same path as ``report``: a single item can be chart
 *   or report; both render server-side to a standalone HTML page
 *   (Chart.js canvas for charts, full report HTML for reports) and
 *   we embed the response in an iframe.
 * - ``text`` — escaped markdown-lite content rendered inline as a
 *   single block with ``<br/>`` line breaks. No fetch needed.
 *
 * Why axios-fetch + blob URL instead of a direct ``<iframe src=URL>``
 * (批 14.7 fix): the backend's ``get_current_user`` accepts only
 * ``Authorization: Bearer <jwt>`` (no cookie / query-param fallback —
 * the ``?token=`` fallback was removed in ``515bbd9``). Browser
 * iframe navigations don't carry the header axios attaches, so the
 * direct-``<iframe>`` approach 401s silently and the cell shows
 * blank. We fetch via axios (which adds the header), wrap the HTML
 * in a ``Blob`` and pass the resulting ``blob:`` URL to the iframe
 * — blob URLs don't need auth, so the iframe loads without it.
 *
 * Kept intentionally tiny — the heavy lifting for the full-grid
 * preview lives server-side at
 * :func:`render_dashboard_html` (``POST /dashboards/{id}/preview``).
 */

import { Alert, Spin, Typography } from 'antd';
import { useEffect, useState } from 'react';

import { dashboardApi } from '../api';
import type { DashboardItem } from '../types';

const { Title } = Typography;

export interface DashboardItemCardProps {
  item: DashboardItem;
}

export function DashboardItemCard({ item }: DashboardItemCardProps) {
  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: '#fff',
        border: '1px solid #e8e8e8',
        borderRadius: 6,
        overflow: 'hidden',
      }}
    >
      {item.title && (
        <Title
          level={5}
          style={{ margin: '8px 12px', flexShrink: 0 }}
          ellipsis={{ tooltip: item.title }}
        >
          {item.title}
        </Title>
      )}
      <div style={{ flex: 1, minHeight: 0, padding: '0 12px 12px' }}>
        {renderBody(item)}
      </div>
    </div>
  );
}

function renderBody(item: DashboardItem) {
  // ``text`` is plain HTML — no fetch, no iframe, just render inline.
  if (item.item_type === 'text') {
    return (
      <div
        style={{
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          padding: '8px 0',
          color: '#333',
          lineHeight: 1.6,
        }}
      >
        {item.text_content ?? ''}
      </div>
    );
  }
  // ``report`` and ``chart`` both render through the same
  // /dashboards/{id}/items/{item_id}/preview endpoint — the inner
  // ``render_dashboard_item_html`` dispatches on ``item_type``.
  if (item.item_type === 'report' || item.item_type === 'chart') {
    return (
      <IframeBody dashboardId={item.dashboard_id} itemId={item.id} />
    );
  }
  return <Alert type="warning" message={`未知 item_type: ${item.item_type}`} />;
}

interface IframeBodyProps {
  dashboardId: number;
  itemId: number;
}

function IframeBody({ dashboardId, itemId }: IframeBodyProps) {
  // Blob URL lifecycle (批 14.7): fetch the preview HTML via axios,
  // wrap in a Blob, hand the blob URL to the iframe. On unmount /
  // item change, revoke the previous URL so we don't leak memory —
  // dashboard view can have dozens of items mounted simultaneously.
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const created: string[] = [];
    dashboardApi
      .previewItem(dashboardId, itemId)
      .then((html) => {
        if (cancelled) return;
        const blob = new Blob([html], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        created.push(url);
        setBlobUrl(url);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : '加载失败');
      });
    return () => {
      cancelled = true;
      for (const url of created) URL.revokeObjectURL(url);
    };
  }, [dashboardId, itemId]);

  return (
    <div style={{ position: 'relative', height: '100%', minHeight: 120 }}>
      {!blobUrl && !error && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(255,255,255,0.6)',
          }}
        >
          <Spin />
        </div>
      )}
      {error ? (
        <Alert type="error" message={error} />
      ) : (
        // ``sandbox="allow-scripts"`` so the inlined Chart.js
        // ``new Chart(...)`` runs inside the iframe — without it the
        // canvas stays blank. ``allow-same-origin`` is intentionally
        // omitted so a malicious snippet can't reach the parent
        // document's storage / cookies. Matches the report preview
        // contract (``ReportPreview.tsx``).
        <iframe
          src={blobUrl ?? 'about:blank'}
          title={`dashboard-item-${itemId}`}
          sandbox="allow-scripts"
          style={{ width: '100%', height: '100%', border: 0 }}
        />
      )}
    </div>
  );
}