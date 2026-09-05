// API Types matching backend Pydantic schemas

// ---- RBAC (批 9) ----
// Coarse-grained roles. Resource-level ACL lives server-side in
// DataSourceAccess / ReportAccess; these strings only gate UI affordances
// (e.g. "can I see the New Data Source button").
export type UserRole = 'admin' | 'editor' | 'viewer';

/** Mirrors backend ``ReportVisibility`` Literal (批 9.4).
 *  批 13 adds ``org`` — same-org viewers only. Backed by the
 *  ``User.org_id`` field (批 9.1) and only enabled when the
 *  operator sets ``DEFAULT_ORG_ID``; otherwise the API treats
 *  ``org`` rows as NULL-on-either-side mismatches. */
export type ReportVisibility = 'public' | 'private' | 'org';

/** Permission tier for a per-report share row. */
export type ReportSharePermission = 'read' | 'write';

// ---- Notification config (批 6b.4 / 8.3 / 8.4) ----
// Mirrors the Pydantic discriminated union in
// ``app.schemas.notification``. The ``type`` field discriminates
// the variant — same shape the backend uses to dispatch at send
// time. We type the variants explicitly (rather than keeping the
// ``Report.notification_config`` field as ``Record<string, unknown>``)
// so the Subscription / Scheduler forms can render the right
// inputs per provider without a runtime introspection pass.

export type NotificationType = 'none' | 'webhook' | 'email' | 'dingtalk' | 'feishu' | 'wechatwork';

export interface WebhookConfig {
  type: 'webhook';
  url: string;
  secret?: string | null;
}

export interface EmailConfig {
  type: 'email';
  to: string[];
  subject: string;
}

export interface DingTalkConfig {
  type: 'dingtalk';
  webhook_url: string;
  secret?: string | null;
}

export interface FeishuConfig {
  type: 'feishu';
  webhook_url: string;
  secret?: string | null;
}

export interface WeChatWorkConfig {
  type: 'wechatwork';
  webhook_url: string;
}

export type NotificationConfig =
  WebhookConfig | EmailConfig | DingTalkConfig | FeishuConfig | WeChatWorkConfig;

// ---- Subscriptions (批 8.3) ----
// Per-user, per-report, per-cron subscription. The backend owns
// the APScheduler reconciliation; the frontend only shows the
// owner-scoped CRUD surface. See
// ``app.models.report_subscription.ReportSubscription``.

export interface ReportSubscription {
  id: number;
  owner_user_id: number;
  report_id: number;
  cron_expression: string;
  parameters: Record<string, unknown> | null;
  notification_config: NotificationConfig | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  next_run_at: string | null;
}

export interface ReportSubscriptionCreate {
  report_id: number;
  cron_expression: string;
  parameters?: Record<string, unknown> | null;
  notification_config?: NotificationConfig | null;
}

export interface ReportSubscriptionUpdate {
  cron_expression?: string;
  parameters?: Record<string, unknown> | null;
  notification_config?: NotificationConfig | null;
  is_active?: boolean;
}

/** Mirrors `GET /auth/me` response shape (post 批 9.1). */
export interface CurrentUser {
  username: string;
  user_id: number;
  role: UserRole;
  /** Reserved for a future multi-tenant deployment; today always `null`. */
  org_id: number | null;
}

/** Single user row from ``GET /users`` (A3, post-批-report-versioning).
 *
 * The report-versioning UI calls this endpoint to resolve the
 * ``created_by`` foreign key on each ``ReportVersionSummary`` into a
 * human-readable ``username`` instead of a raw user id. ``id`` is
 * the only stable foreign key the UI holds; ``username`` is the
 * display projection.
 */
export interface UserSummary {
  id: number;
  username: string;
  role: UserRole;
}

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
  owner_user_id?: number | null;
  org_id?: number | null;
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

export type DataSourceGrantPermission = 'read' | 'write';

/** One row in :class:`DataSourceAccess` (批 9.3). */
export interface DataSourceGrant {
  id: number;
  data_source_id: number;
  user_id: number;
  permission: DataSourceGrantPermission;
  granted_by?: number | null;
  created_at?: string;
}

export interface DataSourceGrantCreate {
  user_id: number;
  permission: DataSourceGrantPermission;
}

// ---- Report shares (批 9.4) ----
// Mirrors the DataSource grant shape: one row per (report_id, user_id).
// Upsert semantics — POSTing twice with the same user_id overwrites
// the permission rather than failing the unique constraint.
export interface ReportShare {
  id: number;
  report_id: number;
  user_id: number;
  permission: ReportSharePermission;
  granted_by?: number | null;
  created_at?: string;
}

export interface ReportShareCreate {
  user_id: number;
  permission: ReportSharePermission;
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
export type OperatorType =
  '=' | '!=' | '>' | '>=' | '<' | '<=' | 'LIKE' | 'IN' | 'IS NULL' | 'IS NOT NULL';

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
  notification_config?: NotificationConfig | null;
  // 批 9.4 — owner / org / visibility. All optional so older
  // pre-9.4 callers that don't read these fields still typecheck.
  // Backfilled server-side: existing rows default to admin-owned
  // and public visibility.
  owner_user_id?: number | null;
  org_id?: number | null;
  visibility?: ReportVisibility;
  // 批 10 demo-badge: True iff the row was inserted by
  // scripts/seed_reports.py. ReportList renders a "示例" Tag when true
  // so operators can tell seed scaffolding apart from reports they
  // authored themselves. Backend never exposes a write path for this
  // flag, so end-users can't tag their own reports as demos.
  is_demo?: boolean;
  // 批 13 template marketplace — flags rows in the public template pool.
  // ``is_template`` rows are surfaced on ``/reports/templates``; the
  // other two fields are the grouping tag and the upstream report id
  // for forks (so a fork remembers which template it came from). All
  // three are read-only on the wire — only ``save-as-template`` and
  // ``from-template`` mutate them, and only on the server side.
  is_template?: boolean;
  template_category?: string | null;
  template_source_id?: number | null;
  created_at?: string;
  updated_at?: string;
  /** 批 3 — optimistic-concurrency counter. ``null`` for legacy rows
   *  that pre-date the column; the frontend treats ``null`` as
   *  "no If-Match available" and skips the precondition header. */
  version?: number | null;
  items: ReportItem[];
}

/** 批 3 — body shape returned by the server on 412 Precondition
 *  Failed. Mirrors the backend ``VersionConflict`` Pydantic model.
 *  FastAPI wraps this in ``{detail: VersionConflict}`` per the
 *  HTTPException convention. */
export interface VersionConflictBody {
  message: string;
  current: Report;
}

/** 批 3 — typed error thrown by ``useUpdateReport`` when the
 *  server returns 412. ``current`` carries the post-conflict state
 *  so the editor's ConflictModal can render a diff without a
 *  second round-trip. */
export class VersionConflictError extends Error {
  public readonly current: Report;

  constructor(message: string, current: Report) {
    super(message);
    this.name = 'VersionConflictError';
    this.current = current;
  }
}

/** Type guard — ``unknown`` from a mutation ``onError`` callback
 *  is a pain to narrow without this. */
export function isVersionConflict(err: unknown): err is VersionConflictError {
  return err instanceof VersionConflictError;
}

/** Body for ``POST /reports/{id}/save-as-template`` (批 13). */
export interface SaveAsTemplateRequest {
  visibility: ReportVisibility;
  category?: string | null;
}

/** Body for ``POST /reports/{id}/from-template`` (批 13).
 *  ``name`` is optional; the backend picks ``<template> (副本)`` with
 *  a numeric suffix on collision. */
export interface ForkFromTemplateRequest {
  name?: string | null;
}

/** Filters for ``GET /reports/templates`` (批 13). Mirrors the backend
 *  Query params: ``category`` / ``data_source_id`` / ``visibility``
 * / ``q``. All fields optional — backend treats missing as
 *  "no filter on this dimension". */
export interface ReportTemplatesFilters {
  category?: string;
  data_source_id?: number;
  visibility?: ReportVisibility;
  q?: string;
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
  // 批 9.4 — default private. Caller can opt into public when
  // broadcasting a report; the backend rejects anything other than
  // 'public' | 'private' via the ``ReportVisibility`` Literal.
  visibility?: ReportVisibility;
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
  visibility?: ReportVisibility;
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
// `pdf` joined in 批 8.1 alongside Excel; HTML stays synchronous
// (preview is small and the iframe needs an immediate response).
export type JobOutputFormat = 'excel' | 'pdf';

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

// ---- Audit log (批 9.5 / 9.6) ----
// Mirrors `AuditLogResponse` in backend/app/schemas/audit.py. The
// fields are nullable because `actor_user_id` / `target_id` go NULL
// when the original user / row is deleted, and `before` / `after`
// are absent on create-only events. The `before`/`after` shapes
// are deliberately typed as `Record<string, unknown> | null` — their
// concrete shape depends on `target_type`, which we don't try to
// model on the TS side.
export interface AuditLog {
  id: number;
  actor_user_id: number | null;
  action: string;
  target_type: string;
  target_id: number | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface AuditLogListResponse {
  items: AuditLog[];
  total: number;
  limit: number;
  offset: number;
}

/** Query params for `GET /audit-logs`. All fields are optional; the
 *  backend treats null/missing as "no filter on this dimension". The
 *  page component owns the filter form state and passes it straight
 *  through to the API client. */
export interface AuditLogFilters {
  actor_user_id?: number;
  action?: string;
  target_type?: string;
  target_id?: number;
  /** Cross-reference a single HTTP request — useful when an operator
   *  has a request id from a log line and wants to see every audit
   *  row emitted under that id. */
  request_id?: string;
  /** Filter by client IP (compliance / abuse investigation). */
  ip_address?: string;
  /** ISO datetime inclusive lower bound on `created_at`. */
  since?: string;
  /** ISO datetime inclusive upper bound on `created_at`. */
  until?: string;
  limit?: number;
  offset?: number;
}

// ============ Report Versions (批 report-versioning) ============

export interface ReportVersionItem {
  id: number;
  name: string;
  item_type: ItemType;
  order_index: number;
  table_name?: string;
  fields?: string[];
  where_conditions?: WhereCondition[];
  group_by?: string[];
  order_by?: OrderByItem[];
  limit?: number;
  display_config?: DisplayConfig;
  custom_sql?: string;
}

export interface ReportVersionParameter {
  id: number;
  name: string;
  label: string;
  type: string;
  required: boolean;
  default?: unknown;
  options?: string[];
  order_index: number;
}

export interface ReportVersionSummary {
  id: number;
  report_id: number;
  version_number: number;
  label?: string | null;
  is_pinned: boolean;
  created_by?: number | null;
  created_at: string;
}

export interface ReportVersionResponse extends ReportVersionSummary {
  name: string;
  description?: string | null;
  data_source_id: number;
  layout_config?: Record<string, unknown>;
  is_scheduled: boolean;
  cron_expression?: string | null;
  schedule_description?: string | null;
  notification_config?: Record<string, unknown> | null;
  output_formats?: string[];
  is_active: boolean;
  visibility: string;
  owner_user_id?: number | null;
  org_id?: number | null;
  items: ReportVersionItem[];
  parameters: ReportVersionParameter[];
}

export interface ReportVersionCreate {
  label?: string;
}

export interface FieldChange {
  field: string;
  old_value: unknown;
  new_value: unknown;
}

export interface ItemDiff {
  name: string;
  changes: FieldChange[];
}

export interface ParameterDiff {
  name: string;
  changes: FieldChange[];
}

export interface ReportVersionDiff {
  base_version: number;
  target_version?: number | null;
  report_changes: FieldChange[];
  items_added: ReportVersionItem[];
  items_removed: ReportVersionItem[];
  items_modified: ItemDiff[];
  parameters_added: ReportVersionParameter[];
  parameters_removed: ReportVersionParameter[];
  parameters_modified: ParameterDiff[];
}

export interface ReportVersionRestoreResponse {
  report: Report;
}

// ============ Admin Metrics (批 12) ============
//
// Mirrors backend/app/schemas/admin_metrics.py — the admin monitoring
// page uses these for the live pool-metrics dashboard.

export type Health = 'green' | 'yellow' | 'red';

export interface HistoryBucket {
  bucket_ts: number; // unix seconds at bucket start
  checkouts: number;
  checkins: number;
  invalidations: number;
}

export interface DataSourcePoolStats {
  data_source_id: number;
  name: string;
  db_type: string;
  active: number;
  pool_size: number;
  checkouts_total: number;
  checkins_total: number;
  invalidations_total: number;
  timeouts_total: number;
  avg_held_ms: number;
  timeout_rate: number;
  health: Health;
  history: HistoryBucket[];
}

export interface HealthSummary {
  green: number;
  yellow: number;
  red: number;
  total: number;
}

export interface AdminMetricsResponse {
  pools: DataSourcePoolStats[];
  health_summary: HealthSummary;
  generated_at: string;
}

// ---- Admin DataSource mutations (批 E) ----
//
// Mirrors ``backend/app/schemas/admin_data_source.py``. Used by the
// "轮换密码" admin-only flow on the DataSource list page.
export type RotationMethod = 'admin_supplied' | 'server_generated';

export interface RotatePasswordResponse {
  data_source_id: number;
  rotation_method: RotationMethod;
  rotated_at: string;
  /** Plaintext — ONLY populated when ``rotation_method === 'server_generated'``.
   *  The admin must copy it immediately; the server does not retain it. */
  generated_password: string | null;
}

// ---- Dashboard (批 14) ----
// Mirrors backend Pydantic schemas in ``app.schemas.dashboard``
// plus the corresponding ORM models in ``app.models.dashboard`` /
// ``dashboard_access`` / ``dashboard_subscription``.
//
// A Dashboard is a grid of items. Items come in three flavours —
// ``report`` (transitive DS via referenced Report), ``chart`` (direct
// DS + SQL config), and ``text`` (free-form HTML-escaped markdown).
// The grid uses react-grid-layout's 12-column model; the backend
// stores ``x/y/w/h`` per item and ``PATCH /dashboards/{id}/items/layout``
// is the batch update path used by ``onLayoutChange``.
//
// ACL: same shape as Report — owner + visibility + per-user grants.
// Write-grant propagates to share management (same transitive model).

export type DashboardVisibility = 'public' | 'private' | 'org';
export type DashboardSharePermission = 'read' | 'write';
export type DashboardItemType = 'report' | 'chart' | 'text';

export interface DashboardItem {
  id: number;
  dashboard_id: number;
  item_type: DashboardItemType;
  title?: string | null;
  order_index: number;
  x: number;
  y: number;
  w: number;
  h: number;
  // type="report"
  report_id?: number | null;
  // type="chart"
  data_source_id?: number | null;
  table_name?: string | null;
  fields: string[];
  where_conditions: WhereCondition[];
  group_by: string[];
  order_by: OrderByItem[];
  limit?: number | null;
  display_config?: DisplayConfig | null;
  custom_sql?: string | null;
  // type="text"
  text_content?: string | null;
  // common
  parameters: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface DashboardItemCreate {
  item_type: DashboardItemType;
  title?: string | null;
  order_index?: number;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  report_id?: number | null;
  data_source_id?: number | null;
  table_name?: string | null;
  fields?: string[];
  where_conditions?: WhereCondition[];
  group_by?: string[];
  order_by?: OrderByItem[];
  limit?: number | null;
  display_config?: DisplayConfig | null;
  custom_sql?: string | null;
  text_content?: string | null;
  parameters?: Record<string, unknown>;
}

export interface DashboardItemUpdate {
  title?: string | null;
  order_index?: number;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  report_id?: number | null;
  data_source_id?: number | null;
  table_name?: string | null;
  fields?: string[];
  where_conditions?: WhereCondition[];
  group_by?: string[];
  order_by?: OrderByItem[];
  limit?: number | null;
  display_config?: DisplayConfig | null;
  custom_sql?: string | null;
  text_content?: string | null;
  parameters?: Record<string, unknown>;
}

/** One row of the batch ``PATCH /dashboards/{id}/items/layout`` body.
 *  Mirrors backend ``DashboardItemLayoutEntry``. */
export interface DashboardItemLayoutEntry {
  item_id: number;
  x: number;
  y: number;
  w: number;
  h: number;
  order_index?: number;
}

export interface Dashboard {
  id: number;
  name: string;
  description?: string | null;
  visibility: DashboardVisibility;
  owner_user_id?: number | null;
  owner_username?: string | null;
  org_id?: number | null;
  can_edit?: boolean;
  item_count?: number | null;
  items: DashboardItem[];
  created_at?: string;
  updated_at?: string;
}

export interface DashboardCreate {
  name: string;
  description?: string;
  visibility?: DashboardVisibility;
  items?: DashboardItemCreate[];
}

export interface DashboardUpdate {
  name?: string;
  description?: string;
  visibility?: DashboardVisibility;
}

export interface DashboardShare {
  id: number;
  dashboard_id: number;
  user_id: number;
  permission: DashboardSharePermission;
  granted_by?: number | null;
  created_at?: string;
}

export interface DashboardShareCreate {
  user_id: number;
  permission: DashboardSharePermission;
}

// ---- Reverse-link references (D 双向 link) ----
// Tiny shapes returned by ``/reports/{id}/dashboards``,
// ``/data-sources/{id}/reports``, and ``/data-sources/{id}/dashboards``.
// Mirrors :mod:`app.schemas.reverse_link`; the backend intentionally
// returns only id + name (+ the bits the UI needs to render badges
// and "used by N items" labels) so the listings stay cheap even
// when a single DS is referenced by hundreds of dashboards.

export interface ReportRef {
  id: number;
  name: string;
  visibility: ReportVisibility;
  is_active?: boolean | null;
}

export interface DataSourceRef {
  id: number;
  name: string;
  db_type: string;
}

export interface DashboardRef {
  id: number;
  name: string;
  visibility: DashboardVisibility;
  item_count?: number | null;
}

// ---- Global command-palette search (批 A) ----
// Three grouped result lists returned by ``GET /search``. The palette
// renders one round-trip per keystroke; per-kind caps are enforced
// server-side. Snake-case ``data_sources`` mirrors the resource path
// (``/data-sources``) and the ``dataSourceApi`` naming.

export interface SearchResponse {
  reports: ReportRef[];
  dashboards: DashboardRef[];
  data_sources: DataSourceRef[];
}

export interface DashboardSubscription {
  id: number;
  owner_user_id: number;
  dashboard_id: number;
  cron_expression: string;
  parameters: Record<string, unknown> | null;
  notification_config: NotificationConfig | null;
  is_active: boolean;
  created_at: string;
  updated_at?: string | null;
  last_run_at?: string | null;
  next_run_at?: string | null;
}

export interface DashboardSubscriptionCreate {
  dashboard_id: number;
  cron_expression: string;
  parameters?: Record<string, unknown> | null;
  notification_config?: NotificationConfig | null;
}

export interface DashboardSubscriptionUpdate {
  cron_expression?: string;
  parameters?: Record<string, unknown> | null;
  notification_config?: NotificationConfig | null;
  is_active?: boolean;
}

// ============ Admin user-management (批 user-management S3+S4) ============
//
// Mirror of backend Pydantic schemas in `backend/app/schemas/user.py`:
// - UserCreate / UserUpdate / UserResponse / PasswordResetRequest /
//   PasswordResetResponse / UserListResponse (S1, schemas/user.py:85-205)
// - GrantSummaryItem / UserAclView / AdminGrantCreate (S2, schemas/user.py:213-275)
// The admin-only endpoints live under /admin/users and /admin/grants; gated
// server-side by admin_required. Frontend mirrors the gate via RequireAdmin
// in App.tsx. The literal types below are kept separate from the existing
// DataSourceGrantPermission / ReportSharePermission / DashboardSharePermission
// so a future cleanup can collapse them — same string union today, but each
// has its own audit convention.
export type AdminUserRole = 'admin' | 'editor' | 'viewer';
export type AdminResourceType = 'data_source' | 'report' | 'dashboard';
export type AdminGrantPermission = 'read' | 'write';
export type PasswordResetMethod = 'admin_supplied' | 'server_generated';

export interface UserResponse {
  id: number;
  username: string;
  role: AdminUserRole;
  disabled: boolean;
  org_id?: number | null;
  created_at?: string | null;
  last_login_at?: string | null;
}

export interface UserCreate {
  username: string;
  password: string;
  role: AdminUserRole;
}

export interface UserUpdate {
  role?: AdminUserRole;
  disabled?: boolean;
}

export interface UserListResponse {
  items: UserResponse[];
  total: number;
  limit: number;
  offset: number;
}

export interface PasswordResetRequest {
  /** Empty/null → server generates; otherwise admin-supplied plaintext. */
  new_password?: string | null;
}

export interface PasswordResetResponse {
  user_id: number;
  rotation_method: PasswordResetMethod;
  reset_at: string;
  /** Non-null ONLY for server_generated; for admin_supplied we deliberately
   *  do not echo the plaintext (admin already knows it). */
  generated_password: string | null;
}

/** One grant row, normalised across DataSource / Report / Dashboard.
 *
 *  Mirrors backend `GrantSummaryItem` (schemas/user.py:213). `grant_id` is
 *  the underlying access-row PK so the admin UI can drive the centralised
 *  DELETE /admin/grants/{resource_type}/{grant_id} without re-resolving. */
export interface GrantSummaryItem {
  resource_type: AdminResourceType;
  resource_id: number;
  resource_name?: string | null;
  grant_id: number;
  permission: AdminGrantPermission;
  granted_by?: number | null;
  granted_by_username?: string | null;
  created_at?: string | null;
}

/** Envelope for GET /admin/users/{id}/grants. subject_type is always
 *  "user" from this endpoint today; the wider union is forward-compatible
 *  with the per-resource counterpart. */
export interface UserAclView {
  subject_type: 'user';
  subject_id: number;
  grants: GrantSummaryItem[];
}

export interface AdminGrantCreate {
  resource_type: AdminResourceType;
  resource_id: number;
  target_user_id: number;
  permission: AdminGrantPermission;
}
