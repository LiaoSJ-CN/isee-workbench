/** Schema browser — left-rail tree of tables and columns for the
 * DataExplorer. Double-clicking a column triggers ``onInsertColumn``
 * (DataExplorer wires this to the SqlEditor's ``insertAtCursor``).
 */

import { Tree, Spin, Alert, Empty } from 'antd';
import { TableOutlined, FieldStringOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';

import type { TableInfo } from '../types';

export interface SchemaTreeProps {
  /** Tables fetched from GET /data-sources/{id}/schema. */
  tables: TableInfo[];
  /** Loading flag from the underlying React Query hook. */
  loading?: boolean;
  /** Error from the React Query hook (e.g. 502 upstream unreachable). */
  error?: Error | null;
  /** Fires when the user double-clicks a column node. */
  onInsertColumn: (qualifiedRef: string) => void;
}

interface ColumnLeafMeta {
  kind: 'column';
  /** Fully-qualified ``table.column`` reference. */
  qualified: string;
}

export function SchemaTree({ tables, loading, error, onInsertColumn }: SchemaTreeProps) {
  if (loading) {
    return (
      <div style={{ padding: 16, textAlign: 'center' }}>
        <Spin size="small" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="无法加载 Schema"
        description={error.message}
        style={{ margin: 12 }}
      />
    );
  }

  if (tables.length === 0) {
    return <Empty description="该数据源无表" style={{ padding: 24 }} />;
  }

  const treeData: DataNode[] = tables.map((table) => ({
    key: `table:${table.name}`,
    title: (
      <span style={{ fontWeight: 500 }}>
        <TableOutlined style={{ marginRight: 6 }} />
        {table.name}
        {table.schema_name ? (
          <span style={{ color: '#999', marginLeft: 6, fontSize: 12 }}>({table.schema_name})</span>
        ) : null}
      </span>
    ),
    selectable: false,
    children: table.columns.map((col) => {
      const meta: ColumnLeafMeta = {
        kind: 'column',
        qualified: `${table.name}.${col.name}`,
      };
      return {
        key: `col:${table.name}.${col.name}`,
        title: (
          <span
            style={{ fontSize: 13 }}
            // Double-click = insert at cursor. Avoid ``onDoubleClick``
            // on the Tree itself because Antd's own click handling
            // would steal selection state.
            onDoubleClick={(e) => {
              e.stopPropagation();
              onInsertColumn(meta.qualified);
            }}
          >
            <FieldStringOutlined style={{ marginRight: 6, color: '#722ed1' }} />
            {col.name}
            <span style={{ color: '#999', marginLeft: 6, fontSize: 12 }}>
              {col.type}
              {!col.nullable ? ' NOT NULL' : ''}
            </span>
          </span>
        ),
        isLeaf: true,
        // Stash the qualified ref so external code can also walk
        // the tree (e.g. for a future "Insert with autocomplete").
        data: meta,
      };
    }),
  }));

  return (
    <Tree
      treeData={treeData}
      defaultExpandAll={false}
      showLine
      blockNode
      // Suppress the default "select" highlight on column leaves —
      // the only useful interaction is double-click, and a permanent
      // selection background just makes the rows look busy.
      selectedKeys={[]}
    />
  );
}
