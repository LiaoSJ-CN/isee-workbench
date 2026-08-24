import { Card, Button, Space, Popconfirm } from 'antd';
import {
  TableOutlined,
  BarChartOutlined,
  NumberOutlined,
  FontSizeOutlined,
  DragOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { ReportItem } from '../../types';

export interface SortableItemProps {
  id: string;
  item: ReportItem;
  index: number;
  onEdit: (item: ReportItem) => void;
  onDelete: (itemId: number) => void;
  onMoveUp: (index: number) => void;
  onMoveDown: (index: number) => void;
  isFirst: boolean;
  isLast: boolean;
}

export function SortableItem({
  id,
  item,
  index,
  onEdit,
  onDelete,
  onMoveUp,
  onMoveDown,
  isFirst,
  isLast,
}: SortableItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const getIcon = () => {
    switch (item.item_type) {
      case 'table':
        return <TableOutlined />;
      case 'chart':
        return <BarChartOutlined />;
      case 'metric':
        return <NumberOutlined />;
      case 'text':
        return <FontSizeOutlined />;
      default:
        return <TableOutlined />;
    }
  };

  return (
    <div ref={setNodeRef} style={{ ...style, marginBottom: 8 }}>
      <Card
        size="small"
        style={{
          borderLeft: `3px solid ${
            item.item_type === 'table'
              ? '#1890ff'
              : item.item_type === 'chart'
                ? '#faad14'
                : item.item_type === 'metric'
                  ? '#52c41a'
                  : '#722ed1'
          }`,
        }}
        bodyStyle={{ padding: '8px 12px' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span {...attributes} {...listeners} style={{ cursor: 'grab', padding: '0 4px' }}>
            <DragOutlined />
          </span>

          <span style={{ fontSize: 16 }}>{getIcon()}</span>

          <span style={{ flex: 1, fontWeight: 500 }}>{item.name}</span>

          <span style={{ color: '#999', fontSize: 12 }}>
            {item.item_type === 'table' && `表: ${item.table_name || '-'}`}
            {item.item_type === 'chart' && `图表: ${item.display_config?.chart_type || '-'}`}
            {item.item_type === 'metric' && `指标`}
            {item.item_type === 'text' && `文本`}
          </span>

          <Space size="small">
            <Button
              type="text"
              size="small"
              icon={<ArrowUpOutlined />}
              disabled={isFirst}
              onClick={() => onMoveUp(index)}
            />
            <Button
              type="text"
              size="small"
              icon={<ArrowDownOutlined />}
              disabled={isLast}
              onClick={() => onMoveDown(index)}
            />
            <Button type="text" size="small" onClick={() => onEdit(item)}>
              编辑
            </Button>
            <Popconfirm title="确定删除?" onConfirm={() => onDelete(item.id)}>
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        </div>
      </Card>
    </div>
  );
}
