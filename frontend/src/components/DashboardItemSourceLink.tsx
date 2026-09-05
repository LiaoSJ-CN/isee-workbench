import { Button, Tooltip } from 'antd';
import { LinkOutlined, DatabaseOutlined } from '@ant-design/icons';

import type { DashboardItem } from '../types';

/**
 * Reverse-link entry point on a dashboard item card (D 双向 link).
 *
 * Renders a small icon button in the item's title row that lets a
 * viewer jump straight to the underlying Report (item_type="report")
 * or DataSource (item_type="chart"). Text items have no source — the
 * button is omitted entirely so the affordance doesn't lie.
 *
 * The button is presentation-only: clicking it calls ``onOpen`` and
 * stops propagation, but it does NOT decide where to navigate. The
 * caller (DashboardView / DashboardEdit) owns the navigation policy
 * — see :func:`handleOpenSource` in those files. We keep navigation
 * out of the component so a future "open in side drawer" variant can
 * reuse the same trigger.
 */
export interface DashboardItemSourceLinkProps {
  item: DashboardItem;
  /** Called when the user clicks the link button. Caller decides the
   *  destination (router push, side drawer, etc.). */
  onOpen: (item: DashboardItem) => void;
}

export function DashboardItemSourceLink({ item, onOpen }: DashboardItemSourceLinkProps) {
  // text items have nothing to link to.
  if (item.item_type === 'text') return null;
  const isReport = item.item_type === 'report';
  // Only render when there is something to jump to. A report item
  // without a report_id (data drift) or a chart item without a
  // data_source_id shouldn't pretend there's a target.
  const hasTarget =
    (isReport && item.report_id != null) ||
    (!isReport && item.data_source_id != null);
  if (!hasTarget) return null;

  const tooltip = isReport ? '打开引用的报表' : '打开引用的数据源';

  return (
    <Tooltip title={tooltip}>
      <Button
        type="text"
        size="small"
        icon={isReport ? <LinkOutlined /> : <DatabaseOutlined />}
        data-testid="dashboard-item-source-link"
        aria-label={tooltip}
        onClick={(e) => {
          // Card-level click opens the item editor in edit mode.
          // We want the source link to NOT bubble into that handler.
          e.stopPropagation();
          onOpen(item);
        }}
        style={{ marginLeft: 4 }}
      />
    </Tooltip>
  );
}