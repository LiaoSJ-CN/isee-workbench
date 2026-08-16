// API Types matching backend Pydantic schemas

/** Schema-browser response shape — GET /data-sources/{id}/schema. */
export interface ColumnInfo {
  name: string;
  type: string;
  nullable: boolean;
  description?: string | null;
}

export interface TableInfo {
  name: string;
  schema_name?: string | null;
  columns: ColumnInfo[];
}

export interface DataSourceSchema {
  tables: TableInfo[];
}

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
  port?: number;
  database: string;
  username?: string;
  password?: string;
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
  | 'horizontalBar';
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
  notification_config?: Record<string, unknown> | null;
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
  item_errors?: Record<string, string>;
}

// ---- Async report jobs (批 3a backend, 批 3b frontend) ----

export type JobStatus = 'pending' | 'running' | 'done' | 'failed';
export type JobOutputFormat = 'excel';

export interface ReportJobCreate {
  output_format?: JobOutputFormat;
  parameters?: Record<string, unknown>;
  priority?: number;
}

export interface ReportJob {
  id: number;
  report_id: number;
  status: JobStatus;
  output_format: JobOutputFormat;
  priority: number;
  parameters: Record<string, unknown> | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  file_path: string | null;
  file_url: string | null;
  error: string | null;
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

// ---- Report parameters (批 4a backend, 批 4b frontend) ----
// Mirrors the Pydantic discriminated union (`type` discriminator, 5 variants).

export type ParameterType = 'string' | 'number' | 'date' | 'enum' | 'bool';

interface ReportParameterBase {
  name: string;
  label: string;
  required?: boolean;
  order_index?: number;
}

export type ReportParameterCreate =
  | (ReportParameterBase & { type: 'string'; default?: string | null })
  | (ReportParameterBase & { type: 'number'; default?: number | null })
  | (ReportParameterBase & { type: 'date'; default?: string | null })
  | (ReportParameterBase & { type: 'enum'; options: string[]; default?: string | null })
  | (ReportParameterBase & { type: 'bool'; default?: boolean | null });

// Flat response shape — the backend serialises the discriminated union
// into one record with `type: ParameterType` and optional type-specific
// fields (`options` only for `enum`, `default` per variant).
export interface ReportParameter {
  id: number;
  report_id: number;
  name: string;
  label: string;
  type: ParameterType;
  required: boolean;
  default: unknown;
  options: string[] | null;
  order_index: number;
  created_at?: string;
  updated_at?: string;
}

// All fields optional — `exclude_unset=True` on the backend means
// missing keys are not applied, so this drives both "partial update"
// and "re-type" flows.
export type ReportParameterUpdate = Partial<{
  name: string;
  label: string;
  required: boolean;
  default: unknown;
  options: string[];
  order_index: number;
  type: ParameterType;
}>;

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

// Response shape for `POST /explorer/query`. `success: false` is a
// *result*, not a thrown error — the SQL ran but returned an error
// message. Callers read `error` directly off the response.
export interface QueryResult {
  success: boolean;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  error?: string;
}
