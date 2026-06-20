import { useState, useEffect } from 'react';
import { Table, Select, Button, Space, Card, message, Alert, Spin, Popconfirm, Input } from 'antd';
import { PlayCircleOutlined, SaveOutlined, ClearOutlined, ExportOutlined, DeleteOutlined, PlusOutlined, BranchesOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { DataSource } from '../types';
import { dataSourceApi, explorerApi } from '../api';
import SqlEditor from '../components/SqlEditor';

const { Option } = Select;

// Simple SQL formatter - idempotent (safe to run multiple times)
function formatSql(sql: string): string {
  // 1. 先规范化：移除多余空白，转大写
  const normalized = sql.trim().replace(/\s+/g, ' ');

  // 2. 关键词列表（按长度降序，确保 LEFT JOIN 先于 LEFT 匹配）
  const keywords = [
    'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'OUTER JOIN',
    'ORDER BY', 'GROUP BY', 'HAVING', 'DISTINCT',
    'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'LIMIT',
    'JOIN', 'ON', 'AS', 'UNION', 'ALL',
  ];

  // 3. 在每个关键词前插入换行和缩进
  let result = normalized;
  keywords.forEach((kw) => {
    const regex = new RegExp('\\b' + kw + '\\b', 'gi');
    result = result.replace(regex, '\n' + kw);
  });

  // 4. 移除开头的多余换行，并统一缩进
  return result
    .replace(/^\n+/, '')  // 移除开头的换行
    .split('\n')
    .map((line) => (line.startsWith('  ') ? line : '  ' + line))  // 统一缩进
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

export default function DataExplorer() {
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [selectedDs, setSelectedDs] = useState<number | null>(null);
  const [sql, setSql] = useState('SELECT * FROM gl_revenue LIMIT 20');
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

  useEffect(() => {
    dataSourceApi.list().then((data) => {
      setDataSources(data);
      setSelectedDs((prev) => prev ?? (data.length > 0 ? data[0].id : null));
    }).catch(() => {
      message.error('加载数据源失败');
    });
  }, []);

  // When template changes, update name and mark as not dirty
  useEffect(() => {
    if (selectedTemplateId) {
      const t = templates.find((t) => t.id === selectedTemplateId);
      if (t) {
        setTemplateName(t.name);
        setSql(t.sql);
        setIsDirty(false);
      }
    }
  }, [selectedTemplateId, templates]);

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

    setLoading(true);
    setResult(null);
    try {
      const data = await explorerApi.query(selectedDs, sql);
      setResult(data);
      if (!data.success && data.error) {
        message.error(data.error);
      } else {
        message.success('查询成功，返回 ' + data.row_count + ' 条');
      }
    } catch {
      message.error('查询执行失败');
    } finally {
      setLoading(false);
    }
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
    const csvRows = result.rows.map((row) =>
      result.columns.map((col) => {
        const val = row[col];
        if (val === null || val === undefined) return '';
        const str = String(val);
        return str.includes(',') ? '"' + str.replace(/"/g, '""') + '"' : str;
      }).join(',')
    );
    const csv = [headers, ...csvRows].join('\n');
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

  return (
    <div style={{ padding: 24 }}>
      <h2 style={{ marginBottom: 16 }}>数据探索</h2>

      <Card style={{ marginBottom: 16 }}>
        {/* 数据源选择 */}
        <Space style={{ marginBottom: 16 }} wrap>
          <div>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>数据源</div>
            <Select
              style={{ width: 200 }}
              value={selectedDs}
              onChange={(v) => setSelectedDs(v)}
              placeholder="选择数据源"
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
            <div style={{ marginBottom: 4, fontWeight: 500 }}>模板</div>
            <Space>
              <Select
                style={{ width: 180 }}
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
          <div style={{ marginBottom: 4, fontWeight: 500 }}>
            模板名称 {isDirty && <span style={{ color: '#faad14', fontSize: 12 }}>(有未保存的更改)</span>}
          </div>
          <Input
            placeholder="输入模板名称"
            value={templateName}
            onChange={handleNameChange}
            style={{ maxWidth: 400 }}
          />
        </div>

        {/* SQL 编辑器 */}
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 4, fontWeight: 500 }}>SQL 语句</div>
          <SqlEditor
            key={selectedTemplateId || 'new'}  // 强制模板变化时重新创建编辑器
            value={sql}
            onChange={handleSqlChange}
            height="180px"
            placeholder="输入 SQL (SELECT only)"
          />
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
          {result.success && result.error && (
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
              rowKey={(_, idx) => String(idx)}
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
