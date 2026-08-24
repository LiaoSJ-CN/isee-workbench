import { Card, Button } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { SortableItem } from './SortableItem';
import type { ReportItem } from '../../types';

export interface ItemsTabProps {
  items: ReportItem[];
  onAdd: () => void;
  onEdit: (item: ReportItem) => void;
  onDelete: (itemId: number) => void;
  onMoveUp: (index: number) => void;
  onMoveDown: (index: number) => void;
  onDragEnd: (event: DragEndEvent) => void;
}

export function ItemsTab({
  items,
  onAdd,
  onEdit,
  onDelete,
  onMoveUp,
  onMoveDown,
  onDragEnd,
}: ItemsTabProps) {
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  return (
    <Card
      title="报表项列表"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>
          添加报表项
        </Button>
      }
    >
      <p style={{ color: '#999', marginBottom: 16 }}>拖拽排序，点击编辑按钮配置报表项详情</p>

      {items.length > 0 ? (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext
            items={items.map((i) => `item-${i.id}`)}
            strategy={verticalListSortingStrategy}
          >
            {items.map((item, index) => (
              <SortableItem
                key={`item-${item.id}`}
                id={`item-${item.id}`}
                item={item}
                index={index}
                onEdit={onEdit}
                onDelete={onDelete}
                onMoveUp={() => onMoveUp(index)}
                onMoveDown={() => onMoveDown(index)}
                isFirst={index === 0}
                isLast={index === items.length - 1}
              />
            ))}
          </SortableContext>
        </DndContext>
      ) : (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
          暂无报表项，点击上方按钮添加
        </div>
      )}
    </Card>
  );
}
