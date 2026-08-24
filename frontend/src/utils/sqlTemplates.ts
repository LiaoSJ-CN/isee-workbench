/** Built-in SQL template helpers (批 10.2).

Pulled out of :file:`src/pages/DataExplorer.tsx` so the pure
``categoryOf`` / ``groupTemplatesByCategory`` functions are
importable in vitest without dragging the whole antd page into the
test bundle. The component re-imports these constants — defaults +
helpers — and the component-local definitions are gone.
*/

export interface SavedTemplate {
  id: string;
  name: string;
  sql: string;
}

// Order matters for the dropdown render (left-to-right,
// top-to-bottom) and as the first-match-wins resolution rule for
// ``categoryOf``. Current buckets are disjoint so order is purely
// a display concern.
export const TEMPLATE_CATEGORIES = [
  { id: 'dim', label: '维度表', match: (id: string) => id.startsWith('dim_') },
  { id: 'dwd', label: '业务明细', match: (id: string) => id.startsWith('dwd_') },
  {
    id: 'aggregate',
    label: '聚合分析',
    match: (id: string) => id.startsWith('dws_') || id.startsWith('ads_'),
  },
  { id: 'cross', label: '跨表 JOIN', match: (id: string) => id.startsWith('cross_') },
] as const;

export type TemplateCategoryId = (typeof TEMPLATE_CATEGORIES)[number]['id'];

export function categoryOf(template: Pick<SavedTemplate, 'id'>): TemplateCategoryId | 'custom' {
  for (const c of TEMPLATE_CATEGORIES) {
    if (c.match(template.id)) return c.id;
  }
  return 'custom';
}

export interface TemplateGroup {
  category: { id: TemplateCategoryId | 'custom'; label: string };
  templates: SavedTemplate[];
}

export function groupTemplatesByCategory(templates: SavedTemplate[]): TemplateGroup[] {
  const buckets = new Map<string, SavedTemplate[]>();
  for (const t of templates) {
    const cat = categoryOf(t);
    const list = buckets.get(cat) ?? [];
    list.push(t);
    buckets.set(cat, list);
  }
  const out: TemplateGroup[] = [];
  for (const c of TEMPLATE_CATEGORIES) {
    const list = buckets.get(c.id);
    if (list && list.length > 0) {
      out.push({ category: { id: c.id, label: c.label }, templates: list });
    }
  }
  const custom = buckets.get('custom');
  if (custom && custom.length > 0) {
    out.push({ category: { id: 'custom', label: '自定义' }, templates: custom });
  }
  return out;
}

export const DEFAULT_TEMPLATES: SavedTemplate[] = [
  // ---- DIM ----
  {
    id: 'dim_supplier',
    name: '供应商列表',
    sql: 'SELECT supplier_code, supplier_name, supplier_type, category, region, contact_person, contact_phone, payment_terms, credit_limit, status FROM dim_supplier ORDER BY supplier_code',
  },
  {
    id: 'dim_customer',
    name: '客户列表',
    sql: 'SELECT customer_code, customer_name, customer_type, industry, region, credit_rating, credit_limit, payment_terms, contact_person, status FROM dim_customer ORDER BY customer_code',
  },
  {
    id: 'dim_department',
    name: '部门列表',
    sql: 'SELECT department_code, department_name, manager FROM dim_department ORDER BY department_code',
  },
  {
    id: 'dim_cost_center',
    name: '成本中心',
    sql: 'SELECT cc.cost_center_code, cc.cost_center_name, d.department_name, cc.manager FROM dim_cost_center cc LEFT JOIN dim_department d ON cc.department_id = d.department_id ORDER BY cc.cost_center_code',
  },
  {
    id: 'dim_account',
    name: '会计科目',
    sql: 'SELECT account_code, account_name, account_type, direction, level FROM dim_account ORDER BY account_code',
  },
  // ---- DWD ----
  {
    id: 'dwd_voucher',
    name: '会计凭证',
    sql: 'SELECT voucher_no, voucher_date, voucher_type, summary, total_debit, total_credit, prepared_by, reviewed_by, status FROM dwd_fin_voucher ORDER BY voucher_date DESC, voucher_id LIMIT 100',
  },
  {
    id: 'dwd_voucher_line',
    name: '凭证明细行',
    sql: 'SELECT v.voucher_no, v.voucher_date, l.line_no, a.account_name, l.summary, l.debit_amount, l.credit_amount FROM dwd_fin_voucher_line l JOIN dwd_fin_voucher v ON l.voucher_id = v.voucher_id JOIN dim_account a ON l.account_id = a.account_id ORDER BY v.voucher_date DESC LIMIT 100',
  },
  {
    id: 'dwd_payment',
    name: '收付款流水',
    sql: 'SELECT payment_no, payment_date, payment_type, amount, payment_method, summary, status FROM dwd_fin_payment ORDER BY payment_date DESC LIMIT 100',
  },
  {
    id: 'dwd_invoice',
    name: '发票列表',
    sql: 'SELECT invoice_no, invoice_date, invoice_type, amount_excl_tax, tax_amount, amount_incl_tax, tax_rate, summary, status FROM dwd_fin_invoice ORDER BY invoice_date DESC LIMIT 100',
  },
  {
    id: 'dwd_ar_balance',
    name: '应收账款余额',
    sql: 'SELECT customer_id, invoice_id, orig_amount, paid_amount, balance, issue_date, due_date, aging_bucket, status FROM dwd_fin_ar_balance ORDER BY balance DESC',
  },
  {
    id: 'dwd_ap_balance',
    name: '应付账款余额',
    sql: 'SELECT supplier_id, invoice_id, orig_amount, paid_amount, balance, issue_date, due_date, aging_bucket, status FROM dwd_fin_ap_balance ORDER BY balance DESC',
  },
  // ---- DWS / ADS ----
  {
    id: 'dws_ar_aging',
    name: '应收账龄汇总',
    sql: 'SELECT period_date, customer_id, amount_30d, amount_31_60d, amount_61_90d, amount_over_90d, total_balance FROM dws_fin_ar_aging ORDER BY period_date DESC, customer_id',
  },
  {
    id: 'dws_ap_aging',
    name: '应付账龄汇总',
    sql: 'SELECT period_date, supplier_id, amount_30d, amount_31_60d, amount_61_90d, amount_over_90d, total_balance FROM dws_fin_ap_aging ORDER BY period_date DESC, supplier_id',
  },
  {
    id: 'ads_cashflow',
    name: '月度现金流',
    sql: 'SELECT year_month, inflow, outflow, net_flow, ending_balance FROM ads_fin_cashflow_monthly ORDER BY year_month',
  },
  {
    id: 'ads_pl',
    name: '月度利润表',
    sql: 'SELECT year_month, revenue, cost, expense, operating_profit, net_profit FROM ads_fin_pl_monthly ORDER BY year_month',
  },
  // ---- 跨表 JOIN 演示 ----
  {
    id: 'cross_ar_by_region',
    name: '应收余额按区域汇总',
    sql: 'SELECT c.region, COUNT(*) cnt, ROUND(SUM(a.balance), 2) total_balance FROM dwd_fin_ar_balance a JOIN dim_customer c ON a.customer_id = c.customer_id GROUP BY c.region ORDER BY total_balance DESC',
  },
  {
    id: 'cross_payment_by_supplier',
    name: '供应商付款汇总',
    sql: "SELECT s.supplier_name, s.region, COUNT(*) cnt, ROUND(SUM(p.amount), 2) total_paid FROM dwd_fin_payment p JOIN dim_supplier s ON p.supplier_id = s.supplier_id WHERE p.payment_type = 'payment' GROUP BY s.supplier_name, s.region ORDER BY total_paid DESC",
  },
  {
    id: 'cross_ar_aging_join',
    name: '应收账龄 × 客户名称',
    sql: 'SELECT c.customer_name, c.industry, ag.period_date, ag.total_balance FROM dws_fin_ar_aging ag JOIN dim_customer c ON ag.customer_id = c.customer_id ORDER BY ag.total_balance DESC LIMIT 50',
  },
];
