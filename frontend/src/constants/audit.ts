/**
 * Action / target-type allowlists for the audit log filter UI (批 9.6).
 *
 * Mirrors the canonical lists in
 * :data:`backend.app.services.audit.ALL_ACTIONS` /
 * :data:`ALL_TARGET_TYPES`. The backend is the source of truth — if a
 * new action is added there, append it here so the filter dropdown
 * surfaces it. The backend will happily accept an unknown string
 * (it just won't match rows), so a stale list won't break the page;
 * it just means the dropdown omits the latest action until updated.
 *
 * Keep this file tiny — it intentionally doesn't import the backend
 * schema (the frontend has no runtime access to Python). If the lists
 * grow much past this size, consider auto-syncing them via OpenAPI.
 */

export const AUDIT_ACTIONS = [
  'login',
  'logout',
  'token_refresh',
  'data_source.create',
  'data_source.update',
  'data_source.delete',
  'data_source.grant',
  'data_source.revoke',
  'report.create',
  'report.update',
  'report.delete',
  'report.item.create',
  'report.item.update',
  'report.item.delete',
  'report.item.reorder',
  'report.parameter.create',
  'report.parameter.update',
  'report.parameter.delete',
  'report.share',
  'report.revoke',
  'report.generate',
  'job.enqueue',
  'subscription.create',
  'subscription.update',
  'subscription.delete',
  'subscription.pause',
  'subscription.resume',
  'scheduler.job.create',
  'scheduler.job.delete',
  'scheduler.sync',
  'explorer.query',
] as const;

export const AUDIT_TARGET_TYPES = [
  'session',
  'data_source',
  'data_source_grant',
  'report',
  'report_item',
  'report_parameter',
  'report_share',
  'report_job',
  'report_subscription',
  'scheduler',
  'explorer_query',
] as const;

export type AuditAction = typeof AUDIT_ACTIONS[number];
export type AuditTargetType = typeof AUDIT_TARGET_TYPES[number];
