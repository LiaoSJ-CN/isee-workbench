import { useEffect, useRef, useState } from 'react';
import { Table, Select, Button, Space, Card, message, Alert, Popconfirm, Input, Tag, Layout } from 'antd';
import { PlayCircleOutlined, SaveOutlined, ClearOutlined, ExportOutlined, DeleteOutlined, PlusOutlined, BranchesOutlined, HistoryOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { HistoryEntry } from '../types';
import { formatError } from '../utils/error';
import { csvEscape } from '../utils/csv';
import { DEFAULT_TEMPLATES, groupTemplatesByCategory, type SavedTemplate } from '../utils/sqlTemplates';
import SqlEditor, { type SqlEditorHandle } from '../components/SqlEditor';
import { CardSkeleton } from '../components/Skeleton';
import { SchemaTree } from '../components/SchemaTree';
import { useDataSources } from '../queries/useDataSources';
import { useDataSourceSchema } from '../queries/useDataSourceSchema';
import { useExploreQuery } from '../queries/useExplorer';

const { Option, OptGroup } = Select;
const { Sider, Content } = Layout;

// SQL keyword list, longest-first so multi-word keywords (LEFT JOIN) match
// before their prefixes (JOIN). Module-level: built once, not per call.
const SQL_KEYWORDS = [
  'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'OUTER JOIN',
  'ORDER BY', 'GROUP BY',
  'SELECT', 'FROM', 'WHERE', 'HAVING', 'DISTINCT',
  'AND', 'OR', 'LIMIT', 'JOIN', 'ON', 'AS', 'UNION', 'ALL',
];
// Multi-word keywords need \s+ between words; single words stay literal.
const KEYWORDS_PATTERN = new RegExp(
  '\\b(' + SQL_KEYWORDS.map((kw) => kw.replace(/\s+/g, '\\s+')).join('|') + ')\\b',
  'gi'
);

// Simple SQL formatter - idempotent (safe to run multiple times)
function formatSql(sql: string): string {
  // 规范化空白
  const normalized = sql.trim().replace(/\s+/g, ' ');

  // 单次替换：每个关键词前插入换行
  // String.prototype.replace is safe with stateful `g` regex (no lastIndex use).
  const result = normalized.replace(KEYWORDS_PATTERN, '\n$1');

  return result
    .replace(/^\n+/, '')
    .split('\n')
    .map((line) => (line.startsWith('  ') ? line : '  ' + line))
    .join('\n');
}

// SavedTemplate / DEFAULT_TEMPLATES / categoryOf /
// groupTemplatesByCategory / TEMPLATE_CATEGORIES live in
// ``src/utils/sqlTemplates.ts`` (批 10.2) so vitest can import them
// without pulling the whole antd page into the test bundle.

// Load templates from localStorage or use defaults
function loadTemplates(): SavedTemplate[] {
  try {
    const stored = localStorage.getItem('sqlTemplates:v2');
    if (stored) {
      return JSON.parse(stored);
    }
  } catch {
    // Ignore parse errors
  }
  return DEFAULT_TEMPLATES;
}

// Save templates to localStorage
function saveTemplates(templates: SavedTemplate[]): void {
  localStorage.setItem('sqlTemplates:v2', JSON.stringify(templates));
}

// ============ Execution history (localStorage-backed) ============

// Capped so localStorage (~5MB) can't fill from runaway re-runs.
const HISTORY_MAX_ENTRIES = 100;
const HISTORY_STORAGE_KEY = 'sqlHistory:v1';
// If the same SQL+ds is executed again within this window, the previous
// entry is replaced (moved to top with fresh ts/row_count/error) instead
// of growing the list. Avoids accidental double-click / Cmd+Enter spam.
const HISTORY_DEDUP_WINDOW_MS = 5000;

function loadHistory(): HistoryEntry[] {
  try {
    const stored = localStorage.getItem(HISTORY_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) return parsed as HistoryEntry[];
    }
  } catch {
    // Ignore parse errors
  }
  return [];
}

function appendHistory(history: HistoryEntry[], entry: HistoryEntry): HistoryEntry[] {
  // Dedup: drop any prior entry with the same ds+sql inside the window —
  // the new entry replaces it at the top with the latest ts/result.
  const filtered = history.filter(
    (h) => !(h.ds_id === entry.ds_id && h.sql === entry.sql && entry.ts - h.ts < HISTORY_DEDUP_WINDOW_MS)
  );
  const next = [entry, ...filtered].slice(0, HISTORY_MAX_ENTRIES);
  localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
  return next;
}

function removeHistoryEntry(history: HistoryEntry[], id: string): HistoryEntry[] {
  const next = history.filter((h) => h.id !== id);
  localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
  return next;
}

function clearHistoryStorage(): void {
  localStorage.removeItem(HISTORY_STORAGE_KEY);
}

function newHistoryId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Build a stable row-key from column values. More robust than array index
 * for React reconciliation when the same row appears across re-renders. */
function resultRowKey(record: Record<string, unknown>, columns: string[], index?: number): string {
  const content = columns.slice(0, 4).map((c) => String(record[c] ?? '\x00')).join('\x1f');
  return content || String(index ?? 0);
}

export default function DataExplorer() {
  const { data: dataSources = [] } = useDataSources();
  const execute = useExploreQuery();

  const [selectedDs, setSelectedDs] = useState<number | null>(null);
  // Universal default that runs on every supported backend (sqlite,
  // postgresql, opengauss, dws) — gives new users a friendly placeholder
  // instead of failing because the seed table isn't there.
  const [sql, setSql] = useState("SELECT '请编辑 SQL 后执行查询' AS hint, current_timestamp AS now");
  // Schema-browser data: only fetched once a data source is picked.
  // The hook internally disables itself when ``selectedDs`` is null.
  const schemaQuery = useDataSourceSchema(selectedDs);
  // SqlEditor imperative handle — lets the SchemaTree's double-click
  // handler insert ``table.column`` at the current cursor position.
  const sqlEditorRef = useRef<SqlEditorHandle>(null);

  // Template state — localStorage-backed, OUTSIDE React Query.
  const [templates, setTemplates] = useState<SavedTemplate[]>(() => loadTemplates());
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [templateName, setTemplateName] = useState('');
  const [isDirty, setIsDirty] = useState(false); // Track if current template has unsaved changes

  // Execution history state — localStorage-backed, OUTSIDE React Query.
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());
  const [historyDsFilter, setHistoryDsFilter] = useState<number | null>(null);

  // Auto-select the first data source once the cache populates. One-shot
  // guard via the `initialized` flag prevents this from clobbering a
  // user selection after the first render.
  const [initialized, setInitialized] = useState(false);
  useEffect(() => {
    if (!initialized && dataSources.length > 0 && selectedDs == null) {
      setSelectedDs(dataSources[0].id);
      setInitialized(true);
    }
  }, [dataSources, selectedDs, initialized]);

  // When the selected template changes, load its name/SQL and clear dirty.
  // Only react to selectedTemplateId — reacting to templates would revert
  // the user's in-progress SQL edit back to the stored value on every save
  // (the save updates templates, which re-triggers this effect).
  useEffect(() => {
    if (selectedTemplateId) {
      const t = templates.find((t) => t.id === selectedTemplateId);
      if (t) {
        setTemplateName(t.name);
        setSql(t.sql);
        setIsDirty(false);
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- see comment above
  }, [selectedTemplateId]);

  // Track if current state differs from selected template
  const checkDirty = (newSql: string, newName: string) => {
    if (selectedTemplateId) {
      const t = templates.find((t) => t.id === selectedTemplateId);
      if (t && (t.sql !== newSql || t.name !== newName)) {
        setIsDirty(true);
      } else {
        setIsDirty(false);
      }
    }
  };

  const handleExecute = () => {
    if (!selectedDs) {
      message.warning('请先选择数据源');
      return;
    }
    if (!sql.trim()) {
      message.warning('请输入 SQL');
      return;
    }
    if (execute.isPending) return;

    // Snapshot DS name and timestamp at click-submission time so the 5-second
    // dedup window is measured from when the user triggered execution, not
    // from when the API response arrives.
    const ds = dataSources.find((d) => d.id === selectedDs);
    const dsName = ds?.name || `ds#${selectedDs}`;
    const sqlSnapshot = sql.trim();
    const clickedAt = Date.now();

    execute.mutate(
      { dataSourceId: selectedDs, sql },
      {
        onSuccess: (data) => {
          if (!data.success && data.error) {
            message.error(data.error);
          } else {
            message.success('查询成功，返回 ' + data.row_count + ' 条');
          }
          setHistory((h) =>
            appendHistory(h, {
              id: newHistoryId(),
              ts: clickedAt,
              ds_id: selectedDs,
              ds_name: dsName,
              sql: sqlSnapshot,
              row_count: data.success ? data.row_count : null,
              success: data.success,
              error: data.error,
            })
          );
        },
        onError: (err) => {
          message.error(formatError(err, '查询执行失败'));
          // Network-level failure — still log so user can see what they tried.
          setHistory((h) =>
            appendHistory(h, {
              id: newHistoryId(),
              ts: clickedAt,
              ds_id: selectedDs,
              ds_name: dsName,
              sql: sqlSnapshot,
              row_count: null,
              success: false,
              error: '请求失败',
            })
          );
        },
      },
    );
  };

  // Reload a historical SQL into the editor. Switches the active data source
  // if the entry was executed against a different one.
  const handleLoadFromHistory = (entry: HistoryEntry) => {
    if (entry.ds_id !== selectedDs) {
      setSelectedDs(entry.ds_id);
    }
    setSql(entry.sql);
    // If a template is selected, the loaded SQL may diverge from it.
    if (selectedTemplateId) {
      const t = templates.find((x) => x.id === selectedTemplateId);
      if (!t || t.sql !== entry.sql) {
        setIsDirty(true);
      }
    }
    message.success('已加载历史 SQL，可编辑后再执行');
  };

  const handleClearHistory = () => {
    clearHistoryStorage();
    setHistory([]);
    setHistoryDsFilter(null);
    message.success('历史已清空');
  };

  const handleDeleteHistoryEntry = (id: string) => {
    setHistory((h) => removeHistoryEntry(h, id));
  };

  const handleFormat = () => {
    setSql(formatSql(sql));
    message.success('已格式化');
  };

  const handleSqlChange = (newSql: string) => {
    setSql(newSql);
    checkDirty(newSql, templateName);
  };

  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newName = e.target.value;
    setTemplateName(newName);
    checkDirty(sql, newName);
  };

  const handleSelectTemplate = (id: string | undefined) => {
    if (isDirty) {
      // Could add a confirmation dialog here
      message.warning('当前模板有未保存的更改，请先保存');
      return;
    }
    setSelectedTemplateId(id || null);
    if (!id) {
      // New template
      setTemplateName('');
      setSql('');
      setIsDirty(true);
    } else {
      const t = templates.find((t) => t.id === id);
      if (t) {
        setTemplateName(t.name);
        setSql(t.sql);
        setIsDirty(false);
      }
    }
  };

  const handleSave = () => {
    if (!templateName.trim()) {
      message.warning('请输入模板名称');
      return;
    }
    if (!sql.trim()) {
      message.warning('请输入 SQL 语句');
      return;
    }

    if (selectedTemplateId) {
      // Update existing template
      const newTemplates = templates.map((t) =>
        t.id === selectedTemplateId ? { ...t, name: templateName, sql } : t
      );
      setTemplates(newTemplates);
      saveTemplates(newTemplates);
      setIsDirty(false);
      message.success('模板已更新');
    } else {
      // Create new template
      const newTemplate: SavedTemplate = {
        id: Date.now().toString(),
        name: templateName,
        sql,
      };
      const newTemplates = [...templates, newTemplate];
      setTemplates(newTemplates);
      saveTemplates(newTemplates);
      setSelectedTemplateId(newTemplate.id);
      setIsDirty(false);
      message.success('模板已保存');
    }
  };

  const handleDelete = () => {
    if (!selectedTemplateId) return;

    const newTemplates = templates.filter((t) => t.id !== selectedTemplateId);
    setTemplates(newTemplates);
    saveTemplates(newTemplates);
    setSelectedTemplateId(null);
    setTemplateName('');
    setSql('');
    setIsDirty(false);
    message.success('模板已删除');
  };

  const handleNew = () => {
    if (isDirty) {
      message.warning('当前模板有未保存的更改，请先保存');
      return;
    }
    setSelectedTemplateId(null);
    setTemplateName('');
    setSql('');
    setIsDirty(true);
  };

  const handleExport = () => {
    const r = execute.data;
    if (!r || !r.success || r.rows.length === 0) return;
    const headers = r.columns.join(',');
    const csvRows = r.rows.map((row) =>
      r.columns.map((col) => {
        const val = row[col];
        if (val === null || val === undefined) return '';
        return csvEscape(String(val));
      }).join(',')
    );
    const csv = [headers, ...csvRows].join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'query_' + Date.now() + '.csv';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    message.success('导出成功');
  };

  const columns: ColumnsType<Record<string, unknown>> = execute.data?.columns
    ? execute.data.columns.map((col) => ({
        title: col,
        dataIndex: col,
        key: col,
        width: 150,
        ellipsis: true,
        render: (val: unknown) => {
          if (val === null) return <span style={{ color: '#999' }}>NULL</span>;
          if (val === undefined) return '-';
          return String(val);
        },
      }))
    : [];

  // Apply ds filter to history (newest first, already sorted at insert time).
  const filteredHistory: HistoryEntry[] = historyDsFilter == null
    ? history
    : history.filter((h) => h.ds_id === historyDsFilter);

  const historyColumns: ColumnsType<HistoryEntry> = [
    {
      title: '时间',
      dataIndex: 'ts',
      width: 160,
      render: (ts: number) => new Date(ts).toLocaleString('zh-CN'),
    },
    {
      title: '数据源',
      dataIndex: 'ds_name',
      width: 140,
      ellipsis: true,
    },
    {
      title: 'SQL',
      dataIndex: 'sql',
      ellipsis: true,
      render: (s: string) => (
        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{s}</span>
      ),
    },
    {
      title: '结果',
      width: 110,
      render: (_, entry: HistoryEntry) =>
        entry.success
          ? <Tag color="green">{entry.row_count ?? 0} 行</Tag>
          : <Tag color="red" title={entry.error}>失败</Tag>,
    },
    {
      title: '操作',
      width: 140,
      render: (_, entry: HistoryEntry) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => handleLoadFromHistory(entry)}>
            复用
          </Button>
          <Popconfirm
            title="删除此条历史?"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDeleteHistoryEntry(entry.id)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Layout style={{ minHeight: 'calc(100vh - 64px)', background: 'transparent' }}>
      {/* Left rail: schema browser. 280px is wide enough for column
          names + type strings (e.g. "timestamp NOT NULL") without
          scrolling on a standard 1440px screen. */}
      <Sider
        width={280}
        theme="light"
        style={{ borderRight: '1px solid #f0f0f0', marginRight: 16 }}
        aria-label="数据源 Schema"
      >
        <div style={{ padding: '12px 16px', fontWeight: 500, borderBottom: '1px solid #f0f0f0' }}>
          Schema 浏览器
        </div>
        <SchemaTree
          tables={schemaQuery.data?.tables ?? []}
          loading={schemaQuery.isPending}
          error={schemaQuery.error as Error | null}
          onInsertColumn={(qualified) => sqlEditorRef.current?.insertAtCursor(qualified)}
        />
      </Sider>

      <Content style={{ padding: '0 24px 24px 0' }}>
        <h2 style={{ marginBottom: 16 }}>数据探索</h2>

      <Card style={{ marginBottom: 16 }}>
        {/* 数据源选择 */}
        <Space style={{ marginBottom: 16 }} wrap>
          <div>
            <span style={{ marginBottom: 4, fontWeight: 500, display: 'block' }}>
              数据源
            </span>
            <Select
              style={{ width: 200 }}
              value={selectedDs}
              onChange={(v) => setSelectedDs(v)}
              placeholder="选择数据源"
              aria-label="数据源"
            >
              {dataSources.map((ds) => (
                <Option key={ds.id} value={ds.id}>
                  {ds.name} ({ds.db_type})
                </Option>
              ))}
            </Select>
          </div>

          {/* 模板选择 */}
          <div>
            <span style={{ marginBottom: 4, fontWeight: 500, display: 'block' }}>
              模板
            </span>
            <Space>
              <Select
                style={{ width: 180 }}
                aria-label="模板"
                placeholder="选择或新建模板"
                value={selectedTemplateId}
                onChange={handleSelectTemplate}
                allowClear
              >
                {groupTemplatesByCategory(templates).map((group) => (
                  <OptGroup key={group.category.id} label={group.category.label}>
                    {group.templates.map((t) => (
                      <Option key={t.id} value={t.id}>
                        {t.name}
                      </Option>
                    ))}
                  </OptGroup>
                ))}
              </Select>
              <Button size="small" icon={<PlusOutlined />} onClick={handleNew}>
                新建
              </Button>
              {selectedTemplateId && (
                <Popconfirm
                  title="确定删除此模板?"
                  onConfirm={handleDelete}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                >
                  <Button size="small" danger icon={<DeleteOutlined />}>
                    删除
                  </Button>
                </Popconfirm>
              )}
            </Space>
          </div>
        </Space>

        {/* 模板名称（内联编辑） */}
        <div style={{ marginBottom: 16 }}>
          <span style={{ marginBottom: 4, fontWeight: 500, display: 'block' }}>
            模板名称 {isDirty && <span style={{ color: '#faad14', fontSize: 12 }}>(有未保存的更改)</span>}
          </span>
          <Input
            placeholder="输入模板名称"
            aria-label="模板名称"
            value={templateName}
            onChange={handleNameChange}
            style={{ maxWidth: 400 }}
          />
        </div>

        {/* SQL 编辑器 */}
        <div style={{ marginBottom: 16 }}>
          <span style={{ marginBottom: 4, fontWeight: 500, display: 'block' }}>
            SQL 语句
          </span>
          <div aria-label="SQL 编辑器">
            <SqlEditor
              ref={sqlEditorRef}
              value={sql}
              onChange={handleSqlChange}
              height="180px"
              placeholder="输入 SQL (SELECT only)"
            />
          </div>
        </div>

        {/* 操作按钮 */}
        <Space>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleExecute} loading={execute.isPending}>
            执行查询
          </Button>
          <Button icon={<BranchesOutlined />} onClick={handleFormat}>
            格式化
          </Button>
          <Button icon={<ClearOutlined />} onClick={() => { setSql(''); setIsDirty(true); }}>
            清空
          </Button>
          <Button
            type="default"
            icon={<SaveOutlined />}
            onClick={handleSave}
            disabled={!templateName.trim() || !sql.trim()}
          >
            {selectedTemplateId ? '保存' : '保存为新模板'}
          </Button>
          {execute.data?.success && execute.data.rows.length > 0 && (
            <Button icon={<ExportOutlined />} onClick={handleExport}>
              导出 CSV
            </Button>
          )}
        </Space>
      </Card>

      {/* 执行历史 */}
      <Card
        title={
          <Space>
            <HistoryOutlined />
            <span>执行历史</span>
            <span style={{ color: '#999', fontSize: 12 }}>
              ({filteredHistory.length}{historyDsFilter != null ? ` / ${history.length}` : ''})
            </span>
          </Space>
        }
        extra={
          <Space>
            <Select
              placeholder="按数据源过滤"
              allowClear
              style={{ width: 180 }}
              size="small"
              value={historyDsFilter}
              onChange={(v) => setHistoryDsFilter(v ?? null)}
            >
              {dataSources.map((ds) => (
                <Option key={ds.id} value={ds.id}>{ds.name}</Option>
              ))}
            </Select>
            <Popconfirm
              title="确定清空所有执行历史?"
              description="此操作不可撤销"
              okText="清空"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={handleClearHistory}
            >
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                disabled={history.length === 0}
              >
                清空
              </Button>
            </Popconfirm>
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        {history.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 24, color: '#999' }}>
            暂无执行历史，执行一次查询后会出现在这里
          </div>
        ) : (
          <Table
            columns={historyColumns}
            dataSource={filteredHistory}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (t: number) => '共 ' + t + ' 条' }}
          />
        )}
      </Card>

      {/* 查询结果 */}
      {execute.isPending && (
        <Card>
          <CardSkeleton rows={6} />
        </Card>
      )}

      {execute.data && !execute.isPending && (
        <Card title={execute.data.success ? `查询结果 (${execute.data.row_count} 条)` : '查询错误'}>
          {!execute.data.success && execute.data.error && (
            <Alert
              type="error"
              message="SQL 执行错误"
              description={<pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{execute.data.error}</pre>}
              style={{ marginBottom: 16 }}
            />
          )}

          {execute.data.success && execute.data.rows.length > 0 && (
            <Table
              columns={columns}
              dataSource={execute.data.rows}
              rowKey={(record, idx) => resultRowKey(record, execute.data!.columns, idx)}
              size="small"
              virtual
              scroll={{ x: execute.data.columns.length * 150, y: 500 }}
              pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t: number) => '共 ' + t + ' 条' }}
            />
          )}

          {execute.data.success && execute.data.rows.length === 0 && (
            <Alert type="warning" message="查询成功，但没有返回任何数据" />
          )}
        </Card>
      )}
      </Content>
    </Layout>
  );
}
