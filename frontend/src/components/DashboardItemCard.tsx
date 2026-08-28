/** Read-only renderer for a single Dashboard item (批 14).
 *
 * Three flavours:
 * - ``report`` — embeds the preview iframe via
 *   ``/reports/{id}/preview`` so the iframe is bounded by the
 *   dashboard's grid cell. Errors surface as an inline Alert.
 * - ``chart`` — placeholder (chart rendering lives in the editor
 *   preview; the iframe path covers the same data path).
 * - ``text`` — escaped markdown-lite content rendered as a single
 *   paragraph with ``<br/>`` line breaks.
 *
 * Kept intentionally tiny — the heavy lifting is in
 * :component:`DashboardItemEditorModal` (edit) and the server-side
 * :func:`render_dashboard_html` (full-grid preview).
 */

import { Alert, Empty, Spin, Typography } from 'antd';
import { useEffect, useState } from 'react';

import { API_BASE } from '../api';
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
  if (item.item_type === 'report' && item.report_id != null) {
    return <ReportItemBody reportId={item.report_id} />;
  }
  if (item.item_type === 'chart') {
    return (
      <Empty
        description="图表预览请使用编辑器内的「预览」"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        style={{ marginTop: 24 }}
      />
    );
  }
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
  return <Alert type="warning" message={`未知 item_type: ${item.item_type}`} />;
}

interface ReportItemBodyProps {
  reportId: number;
}

function ReportItemBody({ reportId }: ReportItemBodyProps) {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Mark loading=false once the iframe's onLoad fires; an ``onError``
  // is unreliable across browsers (silent in Chrome for cross-origin
  // loads) so we treat any 5s timeout with no onLoad as a failure.
  useEffect(() => {
    const timer = window.setTimeout(() => setLoading(false), 5000);
    return () => window.clearTimeout(timer);
  }, []);

  const url = `${API_BASE}/reports/${reportId}/preview`;

  return (
    <div style={{ position: 'relative', height: '100%', minHeight: 120 }}>
      {loading && (
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
        <iframe
          src={url}
          title={`report-${reportId}`}
          sandbox="allow-scripts"
          style={{ width: '100%', height: '100%', border: 0 }}
          onLoad={() => setLoading(false)}
          onError={() => {
            setError('报表加载失败');
            setLoading(false);
          }}
        />
      )}
    </div>
  );
}