import { useState, useEffect } from 'react';
import { Table, Select, Button, Space, Card, message, Alert, Spin, Popconfirm, Input, Tag } from 'antd';
import { PlayCircleOutlined, SaveOutlined, ClearOutlined, ExportOutlined, DeleteOutlined, PlusOutlined, BranchesOutlined, HistoryOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { DataSource, HistoryEntry } from '../types';
import { dataSourceApi, explorerApi } from '../api';
import { formatError } from '../utils/error';
import SqlEditor from '../components/SqlEditor';

const { Option } = Select;

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

interface SavedTemplate {
  id: string;
  name: string;
  sql: string;
}

const DEFAULT_TEMPLATES: SavedTemplate[] = [
  // ---- DIM ----
  { id: 'dim_supplier', name: '供应商列表', sql: 'SELECT supplier_code, supplier_name, supplier_type, category, region, contact_person, contact_phone, payment_terms, credit_limit, status FROM dim_supplier ORDER BY supplier_code' },
  { id: 'dim_customer', name: '客户列表', sql: 'SELECT customer_code, customer_name, customer_type, industry, region, credit_rating, credit_limit, payment_terms, contact_person, status FROM dim_customer ORDER BY customer_code' },
  { id: 'dim_department', name: '部门列表', sql: 'SELECT department_code, department_name, manager FROM dim_department ORDER BY department_code' },
  { id: 'dim_cost_center', name: '成本中心', sql: 'SELECT cc.cost_center_code, cc.cost_center_name, d.department_name, cc.manager FROM dim_cost_center cc LEFT JOIN dim_department d ON cc.department_id = d.department_id ORDER BY cc.cost_center_code' },
  { id: 'dim_account', name: '会计科目', sql: 'SELECT account_code, account_name, account_type, direction, level FROM dim_account ORDER BY account_code' },
  // ---- DWD ----
  { id: 'dwd_voucher', name: '会计凭证', sql: 'SELECT voucher_no, voucher_date, voucher_type, summary, total_debit, total_credit, prepared_by, reviewed_by, status FROM dwd_fin_voucher ORDER BY voucher_date DESC, voucher_id LIMIT 100' },
  { id: 'dwd_voucher_line', name: '凭证明细行', sql: 'SELECT v.voucher_no, v.voucher_date, l.line_no, a.account_name, l.summary, l.debit_amount, l.credit_amount FROM dwd_fin_voucher_line l JOIN dwd_fin_voucher v ON l.voucher_id = v.voucher_id JOIN dim_account a ON l.account_id = a.account_id ORDER BY v.voucher_date DESC LIMIT 100' },
  { id: 'dwd_payment', name: '收付款流水', sql: 'SELECT payment_no, payment_date, payment_type, amount, payment_method, summary, status FROM dwd_fin_payment ORDER BY payment_date DESC LIMIT 100' },
  { id: 'dwd_invoice', name: '发票列表', sql: 'SELECT invoice_no, invoice_date, invoice_type, amount_excl_tax, tax_amount, amount_incl_tax, tax_rate, summary, status FROM dwd_fin_invoice ORDER BY invoice_date DESC LIMIT 100' },
  { id: 'dwd_ar_balance', name: '应收账款余额', sql: 'SELECT customer_id, invoice_id, orig_amount, paid_amount, balance, issue_date, due_date, aging_bucket, status FROM dwd_fin_ar_balance ORDER BY balance DESC' },
  { id: 'dwd_ap_balance', name: '应付账款余额', sql: 'SELECT supplier_id, invoice_id, orig_amount, paid_amount, balance, issue_date, due_date, aging_bucket, status FROM dwd_fin_ap_balance ORDER BY balance DESC' },
  // ---- DWS / ADS ----
  { id: 'dws_ar_aging', name: '应收账龄汇总', sql: 'SELECT period_date, customer_id, amount_30d, amount_31_60d, amount_61_90d, amount_over_90d, total_balance FROM dws_fin_ar_aging ORDER BY period_date DESC, customer_id' },
  { id: 'dws_ap_aging', name: '应付账龄汇总', sql: 'SELECT period_date, supplier_id, amount_30d, amount_31_60d, amount_61_90d, amount_over_90d, total_balance FROM dws_fin_ap_aging ORDER BY period_date DESC, supplier_id' },
  { id: 'ads_cashflow', name: '月度现金流', sql: 'SELECT year_month, inflow, outflow, net_flow, ending_balance FROM ads_fin_cashflow_monthly ORDER BY year_month' },
  { id: 'ads_pl', name: '月度利润表', sql: 'SELECT year_month, revenue, cost, expense, operating_profit, net_profit FROM ads_fin_pl_monthly ORDER BY year_month' },
  // ---- 跨表 JOIN 演示 ----
  { id: 'cross_ar_by_region', name: '应收余额按区域汇总', sql: 'SELECT c.region, COUNT(*) cnt, ROUND(SUM(a.balance), 2) total_balance FROM dwd_fin_ar_balance a JOIN dim_customer c ON a.customer_id = c.customer_id GROUP BY c.region ORDER BY total_balance DESC' },
  { id: 'cross_payment_by_supplier', name: '供应商付款汇总', sql: 'SELECT s.supplier_name, s.region, COUNT(*) cnt, ROUND(SUM(p.amount), 2) total_paid FROM dwd_fin_payment p JOIN dim_supplier s ON p.supplier_id = s.supplier_id WHERE p.payment_type = \'payment\' GROUP BY s.supplier_name, s.region ORDER BY total_paid DESC' },
  { id: 'cross_ar_aging_join', name: '应收账龄 × 客户名称', sql: 'SELECT c.customer_name, c.industry, ag.period_date, ag.total_balance FROM dws_fin_ar_aging ag JOIN dim_customer c ON ag.customer_id = c.customer_id ORDER BY ag.total_balance DESC LIMIT 50' },
];

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
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [selectedDs, setSelectedDs] = useState<number | null>(null);
  // Universal default that runs on every supported backend (sqlite,
  // postgresql, opengauss, dws) — gives new users a friendly placeholder
  // instead of failing because the seed table isn't there.
  const [sql, setSql] = useState("SELECT '请编辑 SQL 后执行查询' AS hint, current_timestamp AS now");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{
    success: boolean;
    columns: string[];
    rows: Record<string, unknown>[];
    row_count: number;
    error?: string;
  } | null>(null);

  // Template state
  const [templates, setTemplates] = useState<SavedTemplate[]>(() => loadTemplates());
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [templateName, setTemplateName] = useState('');
  const [isDirty, setIsDirty] = useState(false); // Track if current template has unsaved changes

  // Execution history state
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());
  const [historyDsFilter, setHistoryDsFilter] = useState<number | null>(null);

  useEffect(() => {
    dataSourceApi.list().then((data) => {
      setDataSources(data);
      setSelectedDs((prev) => prev ?? (data.length > 0 ? data[0].id : null));
    }).catch((err: unknown) => {
      message.error(formatError(err, '加载数据源失败'));
    });
  }, []);

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

  const handleExecute = async () => {
    if (!selectedDs) {
      message.warning('请先选择数据源');
      return;
    }
    if (!sql.trim()) {
      message.warning('请输入 SQL');
      return;
    }
    if (loading) return;
    setLoading(true);
    setResult(null);
    // Snapshot DS name and timestamp at click-submission time so the 5-second
    // dedup window is measured from when the user triggered execution, not
    // from when the API response arrives.
    const ds = dataSources.find((d) => d.id === selectedDs);
    const dsName = ds?.name || `ds#${selectedDs}`;
    const sqlSnapshot = sql.trim();
    const clickedAt = Date.now();
    try {
      const data = await explorerApi.query(selectedDs, sql);
      setResult(data);
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
    } catch (err: unknown) {
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
    } finally {
      setLoading(false);
    }
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
    if (!result || result.rows.length === 0) return;
    const headers = result.columns.join(',');
    // RFC 4180: a field needs quoting if it contains the delimiter, a quote,
    // a CR, or an LF. Quotes inside the field are escaped by doubling.
    const csvEscape = (s: string): string =>
      /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    const csvRows = result.rows.map((row) =>
      result.columns.map((col) => {
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

  const columns: ColumnsType<Record<string, unknown>> = result?.columns
    ? result.columns.map((col) => ({
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
    <div style={{ padding: 24 }}>
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
                {templates.map((t) => (
                  <Option key={t.id} value={t.id}>
                    {t.name}
                  </Option>
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
              value={sql}
              onChange={handleSqlChange}
              height="180px"
              placeholder="输入 SQL (SELECT only)"
            />
          </div>
        </div>

        {/* 操作按钮 */}
        <Space>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleExecute} loading={loading}>
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
          {result && result.success && result.rows.length > 0 && (
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
      {loading && (
        <Card>
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
            <p>执行查询中...</p>
          </div>
        </Card>
      )}

      {result && (
        <Card title={result.success ? '查询结果 (' + result.row_count + ' 条)' : '查询错误'}>
          {!result.success && result.error && (
            <Alert
              type="error"
              message="SQL 执行错误"
              description={<pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{result.error}</pre>}
              style={{ marginBottom: 16 }}
            />
          )}

          {result.success && result.rows.length > 0 && (
            <Table
              columns={columns}
              dataSource={result.rows}
              rowKey={(record, idx) => resultRowKey(record, result.columns, idx)}
              size="small"
              scroll={{ x: result.columns.length * 150 }}
              pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t: number) => '共 ' + t + ' 条' }}
            />
          )}

          {result.success && result.rows.length === 0 && (
            <Alert type="warning" message="查询成功，但没有返回任何数据" />
          )}
        </Card>
      )}
    </div>
  );
}
