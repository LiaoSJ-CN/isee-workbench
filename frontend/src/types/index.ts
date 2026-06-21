// API Types matching backend Pydantic schemas

export interface DataSource {
  id: number;
  name: string;
  db_type: 'opengauss' | 'dws' | 'postgresql' | 'sqlite';
  host?: string;
  port?: number;
  database: string;
  username?: string;
  schema_name?: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
}

export interface DataSourceCreate {
  name: string;
  db_type: 'opengauss' | 'dws' | 'postgresql' | 'sqlite';
  host?: string;
  port: number;
  database: string;
  username: string;
  password: string;
  schema_name?: string;
  description?: string;
}

export type ItemType = 'table' | 'chart' | 'text' | 'metric';
export type ChartType =
  | 'bar'
  | 'line'
  | 'pie'
  | 'doughnut'
  | 'radar'
  | 'polarArea'
  | 'scatter'
  | 'bubble'
  | 'area'
  | 'horizontalBar'
  | 'funnel'
  | 'gauge'
  | 'heatmap';
export type OperatorType = '=' | '!=' | '>' | '>=' | '<' | '<=' | 'LIKE' | 'IN' | 'IS NULL' | 'IS NOT NULL';

export interface WhereCondition {
  field: string;
  operator: OperatorType;
  value?: string | number | (string | number)[] | null;
}

export interface OrderByItem {
  field: string;
  direction: 'ASC' | 'DESC';
}

export interface ColumnConfig {
  field: string;
  header?: string;
  format?: string;
  width?: number;
}

export interface DisplayConfig {
  chart_type?: ChartType;
  title?: string;
  subtitle?: string;
  colors?: string[];
  columns?: ColumnConfig[];
  height?: number;
  width?: number;
  content?: string;
  // 图表额外配置 — sub-field keys must match backend `DisplayConfig` (snake_case).
  // Backend silently drops unknown keys (Pydantic extra='ignore'), so camelCase
  // here would cause user toggles to vanish on save.
  show_legend?: boolean;
  legend_position?: 'top' | 'bottom' | 'left' | 'right';
  show_data_label?: boolean;
  show_grid?: boolean;
  stacked?: boolean;
  horizontal?: boolean;
  // 坐标轴配置
  x_axis_field?: string;
  y_axis_fields?: string[];
  // 饼图/环形图配置
  show_percentage?: boolean;
  // 仪表盘配置
  min_value?: number;
  max_value?: number;
  unit?: string;
}

export interface ReportItem {
  id: number;
  report_id: number;
  name: string;
  item_type: ItemType;
  order_index: number;
  table_name?: string;
  fields: string[];
  where_conditions: WhereCondition[];
  group_by: string[];
  order_by: OrderByItem[];
  limit?: number;
  display_config?: DisplayConfig;
  custom_sql?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ReportItemCreate {
  name: string;
  item_type: ItemType;
  order_index?: number;
  table_name?: string;
  fields?: string[];
  where_conditions?: WhereCondition[];
  group_by?: string[];
  order_by?: OrderByItem[];
  limit?: number;
  display_config?: DisplayConfig;
  custom_sql?: string;
}

export interface ReportItemUpdate {
  name?: string;
  item_type?: ItemType;
  order_index?: number;
  table_name?: string;
  fields?: string[];
  where_conditions?: WhereCondition[];
  group_by?: string[];
  order_by?: OrderByItem[];
  limit?: number;
  display_config?: DisplayConfig;
  custom_sql?: string;
}

export interface Report {
  id: number;
  name: string;
  description?: string;
  data_source_id: number;
  layout_config?: Record<string, unknown>;
  output_formats: string[];
  is_active: boolean;
  is_scheduled: boolean;
  cron_expression?: string;
  schedule_description?: string;
  created_at?: string;
  updated_at?: string;
  items: ReportItem[];
}

export interface ReportCreate {
  name: string;
  description?: string;
  data_source_id: number;
  layout_config?: Record<string, unknown>;
  output_formats?: string[];
  is_active?: boolean;
  is_scheduled?: boolean;
  cron_expression?: string;
  schedule_description?: string;
  items?: ReportItemCreate[];
}

export interface ReportUpdate {
  name?: string;
  description?: string;
  data_source_id?: number;
  layout_config?: Record<string, unknown>;
  output_formats?: string[];
  is_active?: boolean;
  is_scheduled?: boolean;
  cron_expression?: string;
  schedule_description?: string;
}

export interface ReportGenerateRequest {
  report_id: number;
  output_format: 'excel' | 'html';
  parameters?: Record<string, unknown>;
}

export interface ReportGenerateResponse {
  success: boolean;
  report_id: number;
  report_name: string;
  output_format: string;
  file_path?: string;
  preview_data?: unknown;
  error?: string;
}

export interface SchedulerJob {
  job_id: string;
  next_run?: string;
  trigger: string;
}

export interface SchedulerStatus {
  is_running: boolean;
  jobs: SchedulerJob[];
}

// One row in the DataExplorer execution history (localStorage-backed).
// `ds_name` is a snapshot, not a live reference — survives the source
// being renamed or deleted.
export interface HistoryEntry {
  id: string;
  ts: number;
  ds_id: number;
  ds_name: string;
  sql: string;
  row_count: number | null;
  success: boolean;
  error?: string;
}
