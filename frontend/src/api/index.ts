import axios from 'axios';
import type {
  AdminMetricsResponse,
  AuditLogFilters,
  AuditLogListResponse,
  CurrentUser,
  DataSource,
  DataSourceCreate,
  DataSourceGrant,
  DataSourceGrantCreate,
  DataSourceSchema,
  QueryResult,
  Report,
  ReportCreate,
  ReportShare,
  ReportShareCreate,
  ReportSubscription,
  ReportSubscriptionCreate,
  ReportSubscriptionUpdate,
  ReportUpdate,
  ReportItem,
  ReportItemCreate,
  ReportItemUpdate,
  ReportGenerateResponse,
  ReportJob,
  ReportJobCreate,
  ReportParameter,
  ReportParameterCreate,
  ReportParameterUpdate,
  ReportVersionCreate,
  ReportVersionDiff,
  ReportVersionResponse,
  ReportVersionRestoreResponse,
  ReportVersionSummary,
  SchedulerStatus,
  SchedulerJob,
  UserSummary,
} from '../types';

// T13: re-export `ReportVersionCreate` so consumers (e.g.
// `useReportVersions.ts`) can import it as a type from `../api`.
// The import block above pulls the type for use in this file's
// function signatures; without this re-export `verbatimModuleSyntax`
// makes the consumer import fail at type-check time.
export type { ReportVersionCreate };

// VITE_API_BASE_URL can be set at build time for production deployments.
// Defaults to '/api' which works with the Vite dev proxy and nginx reverse proxy.
export const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';
const ACCESS_KEY = 'access_token';
const REFRESH_KEY = 'refresh_token';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
});

// Attach the access token to every outbound request.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(ACCESS_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401, try refreshing the access token once, then retry the original
// request. If that fails, clear tokens and bounce to /login. The
// /auth/refresh call itself must not trigger this path.
let refreshing: Promise<string | null> | null = null;

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    const status = err?.response?.status;
    const original = err?.config as (typeof err.config & { _retry?: boolean }) | undefined;
    const onLogin = window.location.pathname.startsWith('/login');
    const isRefreshCall = original?.url?.includes('/auth/refresh');

    if (status === 401 && !onLogin && !isRefreshCall && original && !original._retry) {
      const refreshToken = localStorage.getItem(REFRESH_KEY);
      if (refreshToken) {
        try {
          // Dedupe concurrent refresh calls.
          if (!refreshing) {
            refreshing = api
              .post('/auth/refresh', { refresh_token: refreshToken })
              .then((r) => r.data.access_token as string)
              .finally(() => {
                refreshing = null;
              });
          }
          const newAccess = await refreshing;
          if (newAccess) {
            localStorage.setItem(ACCESS_KEY, newAccess);
            original._retry = true;
            original.headers.Authorization = `Bearer ${newAccess}`;
            return api(original);
          }
        } catch {
          // fall through to logout
        }
      }
      localStorage.removeItem(ACCESS_KEY);
      localStorage.removeItem(REFRESH_KEY);
      window.location.href = '/login';
    }
    return Promise.reject(err);
  },
);

// ============ Auth ============

export const authApi = {
  login: async (
    username: string,
    password: string,
  ): Promise<{ access_token: string; refresh_token: string; token_type: string }> => {
    const { data } = await api.post('/auth/login', { username, password });
    localStorage.setItem(ACCESS_KEY, data.access_token);
    localStorage.setItem(REFRESH_KEY, data.refresh_token);
    return data;
  },
  logout: async (): Promise<{ ok: boolean }> => {
    try {
      const { data } = await api.post('/auth/logout');
      return data;
    } finally {
      localStorage.removeItem(ACCESS_KEY);
      localStorage.removeItem(REFRESH_KEY);
    }
  },
  me: async (): Promise<CurrentUser> => {
    const { data } = await api.get('/auth/me');
    return data;
  },
};

// ============ Data Sources ============

export const dataSourceApi = {
  list: async (): Promise<DataSource[]> => {
    const { data } = await api.get('/data-sources');
    return data;
  },

  get: async (id: number): Promise<DataSource> => {
    const { data } = await api.get(`/data-sources/${id}`);
    return data;
  },

  create: async (payload: DataSourceCreate): Promise<DataSource> => {
    const { data } = await api.post('/data-sources', payload);
    return data;
  },

  update: async (id: number, payload: Partial<DataSourceCreate>): Promise<DataSource> => {
    const { data } = await api.put(`/data-sources/${id}`, payload);
    return data;
  },

  delete: async (id: number): Promise<void> => {
    await api.delete(`/data-sources/${id}`);
  },

  clone: async (id: number, name?: string): Promise<DataSource> => {
    const { data } = await api.post(`/data-sources/${id}/clone`, name ? { name } : {});
    return data;
  },

  test: async (id: number): Promise<{ success: boolean; version: string }> => {
    const { data } = await api.post(`/data-sources/${id}/test`);
    return data;
  },

  /**
   * Schema-browser endpoint — returns the list of user tables and
   * their columns. Optional ``schema`` override targets a specific
   * schema name (e.g. ``public`` for Postgres, ``main`` for SQLite).
   */
  schema: async (id: number, schema?: string): Promise<DataSourceSchema> => {
    const { data } = await api.get(`/data-sources/${id}/schema`, {
      params: schema ? { schema } : {},
    });
    return data;
  },

  // ---- ACL (批 9.3) ----

  /** List every grant on a data source. Owner-or-admin only — a
   *  read grant on the source itself does not let the recipient see
   *  who else has access. Backend enforces the same. */
  listAcl: async (dsId: number): Promise<DataSourceGrant[]> => {
    const { data } = await api.get(`/data-sources/${dsId}/grants`);
    return data;
  },

  /** Grant (or upsert) a permission. Owner-or-admin only. */
  createAcl: async (dsId: number, payload: DataSourceGrantCreate): Promise<DataSourceGrant> => {
    const { data } = await api.post(`/data-sources/${dsId}/grants`, payload);
    return data;
  },

  /** Revoke a grant by id. Owner-or-admin only. */
  revokeAcl: async (grantId: number): Promise<void> => {
    await api.delete(`/data-sources/grants/${grantId}`);
  },
};

/** Minimal user record returned by ``GET /users``.
 *
 * The ``UserSummary`` shape is defined in ``../types`` (single source
 * of truth) and re-imported at the top of this file. ``usersApi.list``
 * exists for the share modals (data source + report) and the
 * report-versioning UI, all of which resolve foreign-key user ids to
 * display usernames client-side. */
export const usersApi = {
  list: async (): Promise<UserSummary[]> => {
    const { data } = await api.get('/users');
    return data;
  },
};

/**
 * Trigger a browser download from an axios `responseType: 'blob'` promise.
 *
 * The endpoints that serve generated files (Excel / HTML) are JWT-gated,
 * so we go through axios (the interceptor attaches the Bearer token) and
 * fetch the payload as a Blob. The wrinkle: when the server returns an
 * error, FastAPI still honours the ``Accept`` we sent and may reply with
 * a JSON body wrapped as a Blob — so we sniff ``content-type`` and
 * unwrap the JSON detail before re-throwing, so the caller's error
 * handler sees a normal ``{detail: ...}`` shape.
 */
async function downloadBlob(responsePromise: Promise<unknown>, filename: string): Promise<void> {
  try {
    const response = (await responsePromise) as {
      headers: Record<string, string>;
      data: Blob | ArrayBuffer;
    };
    const contentType = String(response.headers['content-type'] || '');
    if (contentType.includes('application/json')) {
      const text = await (response.data as Blob).text();
      let detail = text;
      try {
        const parsed = JSON.parse(text);
        detail = parsed.detail || text;
      } catch {
        /* use raw text */
      }
      throw { response: { data: { detail } } };
    }
    const blob = new Blob([response.data]);
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  } catch (err: unknown) {
    if (
      err &&
      typeof err === 'object' &&
      'response' in err &&
      (err as { response?: { data?: unknown; headers?: Record<string, string> } }).response
    ) {
      const axiosErr = err as { response: { data?: unknown; headers?: Record<string, string> } };
      const ct = String(axiosErr.response.headers?.['content-type'] || '');
      if (ct.includes('application/json') && axiosErr.response.data instanceof Blob) {
        const text = await axiosErr.response.data.text();
        let detail = text;
        try {
          const parsed = JSON.parse(text);
          detail = parsed.detail || text;
        } catch {
          /* use raw text */
        }
        throw { response: { data: { detail } } };
      }
    }
    throw err;
  }
}

// ============ Reports ============

export const reportApi = {
  list: async (params?: { is_active?: boolean; data_source_id?: number }): Promise<Report[]> => {
    const { data } = await api.get('/reports', { params });
    return data;
  },

  get: async (id: number): Promise<Report> => {
    const { data } = await api.get(`/reports/${id}`);
    return data;
  },

  create: async (payload: ReportCreate): Promise<Report> => {
    const { data } = await api.post('/reports', payload);
    return data;
  },

  update: async (id: number, payload: ReportUpdate): Promise<Report> => {
    const { data } = await api.put(`/reports/${id}`, payload);
    return data;
  },

  delete: async (id: number): Promise<void> => {
    await api.delete(`/reports/${id}`);
  },

  /** Duplicate a report (batch 10.3). Returns the new report detail
   *  including items — the caller usually wants to navigate straight
   *  into the editor for the new copy. ``name`` is optional; the
   *  backend picks ``<original> (副本)`` when omitted. */
  duplicate: async (id: number, name?: string): Promise<Report> => {
    const { data } = await api.post(`/reports/${id}/duplicate`, name ? { name } : {});
    return data;
  },

  // ---- ACL / shares (批 9.4) ----

  /** List shares on a report. Owner-or-admin only — a write grant
   *  on the report lets the recipient mutate the report itself but
   *  not enumerate who else has access (same semantics as the DS
   *  endpoint). */
  listShares: async (reportId: number): Promise<ReportShare[]> => {
    const { data } = await api.get(`/reports/${reportId}/shares`);
    return data;
  },

  /** Grant (or upsert) a permission. Owner-or-admin only. */
  createShare: async (reportId: number, payload: ReportShareCreate): Promise<ReportShare> => {
    const { data } = await api.post(`/reports/${reportId}/shares`, payload);
    return data;
  },

  /** Revoke a share by id. Owner-or-admin only. */
  revokeShare: async (shareId: number): Promise<void> => {
    await api.delete(`/reports/shares/${shareId}`);
  },

  // Report Items
  createItem: async (reportId: number, payload: ReportItemCreate): Promise<ReportItem> => {
    const { data } = await api.post(`/reports/${reportId}/items`, payload);
    return data;
  },

  updateItem: async (
    reportId: number,
    itemId: number,
    payload: ReportItemUpdate,
  ): Promise<ReportItem> => {
    const { data } = await api.put(`/reports/${reportId}/items/${itemId}`, payload);
    return data;
  },

  reorderItems: async (
    reportId: number,
    items: { item_id: number; order_index: number }[],
  ): Promise<{ updated: number }> => {
    const { data } = await api.patch(`/reports/${reportId}/items/order`, { items });
    return data;
  },

  deleteItem: async (reportId: number, itemId: number): Promise<void> => {
    await api.delete(`/reports/${reportId}/items/${itemId}`);
  },

  // Generation
  generate: async (
    reportId: number,
    outputFormat: 'excel' | 'html',
    parameters?: Record<string, unknown>,
  ): Promise<ReportGenerateResponse> => {
    const { data } = await api.post('/reports/generate', {
      report_id: reportId,
      output_format: outputFormat,
      parameters: parameters || {},
    });
    return data;
  },

  preview: async (
    reportId: number,
    format: 'html' = 'html',
  ): Promise<{ preview_data: unknown }> => {
    const { data } = await api.get(`/reports/${reportId}/preview`, { params: { format } });
    return data;
  },

  previewHtml: async (reportId: number): Promise<string> => {
    const { data } = await api.get(`/reports/${reportId}/preview`, {
      params: { format: 'html' },
      responseType: 'text',
    });
    return data;
  },

  // SEC-11: This returns a raw URL without an auth token. Using it with
  // fetch() or window.open() will 401. Use download() instead, which
  // attaches the Bearer token via the axios interceptor.
  getExportUrl: (reportId: number, format: 'excel' | 'html'): string => {
    return `${API_BASE}/reports/${reportId}/export/${format}`;
  },

  // Fetch via axios (not raw `fetch`) so the request interceptor attaches the
  // Bearer token. The `/export/{format}` endpoint is JWT-gated — a raw fetch
  // without an Authorization header always gets 401.
  download: async (reportId: number, format: 'excel' | 'html', filename: string): Promise<void> => {
    return downloadBlob(
      api.get(`/reports/${reportId}/export/${format}`, { responseType: 'blob' }),
      `${filename}.${format}`,
    );
  },
};

// ============ Scheduler ============

export const schedulerApi = {
  getStatus: async (): Promise<SchedulerStatus> => {
    const { data } = await api.get('/scheduler/status');
    return data;
  },

  sync: async (): Promise<{ jobs_loaded: number; message: string }> => {
    const { data } = await api.post('/scheduler/sync');
    return data;
  },

  getJob: async (reportId: number): Promise<SchedulerJob> => {
    const { data } = await api.get(`/scheduler/jobs/${reportId}`);
    return data;
  },

  createJob: async (
    reportId: number,
    cronExpression: string,
    scheduleDescription?: string,
    notificationConfig?: Record<string, unknown> | null,
    isActive: boolean = true,
  ): Promise<SchedulerJob> => {
    const body = {
      report_id: reportId,
      cron_expression: cronExpression,
      schedule_description: scheduleDescription ?? null,
      notification_config: notificationConfig ?? {},
      is_active: isActive,
    };
    const { data } = await api.post(`/scheduler/jobs/${reportId}`, body);
    return data;
  },

  deleteJob: async (reportId: number): Promise<void> => {
    await api.delete(`/scheduler/jobs/${reportId}`);
  },
};

// ============ Data Explorer ============

export const explorerApi = {
  query: async (dataSourceId: number, sql: string): Promise<QueryResult> => {
    const { data } = await api.post('/explorer/query', {
      data_source_id: dataSourceId,
      sql: sql,
    });
    return data;
  },
};

// ============ Async report jobs (批 3b) ============

/**
 * Backend wraps long-running Excel renders in a :class:`ReportJob` row
 * and exposes `POST /reports/{id}/jobs` (enqueue) + `GET /jobs/{id}`
 * (status). The frontend polls the latter every 2s while
 * pending/running and downloads the produced file via
 * `reportApi.download` once `status === 'done'`.
 */
export const jobsApi = {
  enqueue: async (reportId: number, payload: ReportJobCreate = {}): Promise<ReportJob> => {
    const { data } = await api.post(`/reports/${reportId}/jobs`, payload);
    return data;
  },

  get: async (jobId: number): Promise<ReportJob> => {
    const { data } = await api.get(`/jobs/${jobId}`);
    return data;
  },

  /**
   * Download the worker-produced file for a completed job (批 8.5).
   *
   * Closes the async-export loop: previously the frontend polled
   * ``/jobs/{id}`` until ``status === 'done'`` and then called
   * ``reportApi.download(reportId, ...)`` to download — that endpoint
   * *re-runs* the renderer. With this route the worker output is served
   * directly by basename, so a 30-second render takes 30 seconds end
   * to end, not 60.
   *
   * Caller is responsible for the filename — the server's
   * ``Content-Disposition`` only carries the basename (e.g.
   * ``report_2026-08-16T02-30-00.xlsx``), so passing a friendlier name
   * like ``<report.name>_2026-08-16.xlsx`` here gives the user a
   * nicer file in their downloads folder.
   */
  download: async (jobId: number, filename: string): Promise<void> => {
    return downloadBlob(api.get(`/jobs/${jobId}/download`, { responseType: 'blob' }), filename);
  },
};

// ============ Report parameters (批 4b) ============

/**
 * Per-report typed parameter declarations used by `ReportParameterForm`
 * to render the input form and by `useEnqueueReportJob({ parameters })`
 * to feed the worker thread. CRUD endpoints exposed by the backend in
 * 批 4a — frontend surface landed here.
 */
export const parametersApi = {
  list: async (reportId: number): Promise<ReportParameter[]> => {
    const { data } = await api.get(`/reports/${reportId}/parameters`);
    return data;
  },

  create: async (reportId: number, payload: ReportParameterCreate): Promise<ReportParameter> => {
    const { data } = await api.post(`/reports/${reportId}/parameters`, payload);
    return data;
  },

  update: async (
    reportId: number,
    paramId: number,
    payload: ReportParameterUpdate,
  ): Promise<ReportParameter> => {
    const { data } = await api.put(`/reports/${reportId}/parameters/${paramId}`, payload);
    return data;
  },

  delete: async (reportId: number, paramId: number): Promise<void> => {
    await api.delete(`/reports/${reportId}/parameters/${paramId}`);
  },
};

// ============ Audit log (批 9.5 / 9.6) ============
// Admin-only reader. The backend route is gated by `admin_required`,
// so a 403 here means the current user is not an admin. We do NOT
// set `retry: false` here — a transient 5xx is worth retrying once
// before we show an error.
export const auditLogApi = {
  list: async (filters?: AuditLogFilters): Promise<AuditLogListResponse> => {
    // Strip undefined keys so axios doesn't serialise them as
    // `?undefined=...`. Backend treats missing fields as
    // "no filter on this dimension" (Query(default=None)).
    const params: Record<string, unknown> = {};
    if (filters) {
      for (const [key, value] of Object.entries(filters)) {
        if (value !== undefined && value !== null) params[key] = value;
      }
    }
    const { data } = await api.get('/audit-logs', { params });
    return data;
  },
};

// ============ Subscriptions (批 8.3) ============
// Owner-scoped CRUD on per-user report subscriptions. The backend
// route filters by ``owner_user_id == current_user.id`` so a 404
// here covers both "doesn't exist" and "belongs to someone else"
// (the latter surfaces to the user as "Subscription not found" —
// we don't leak cross-user existence).
export const subscriptionApi = {
  /** List the current user's subscriptions. ``reportId`` is an
   *  optional filter for the per-report subscriptions panel. */
  list: async (reportId?: number, limit = 50, offset = 0): Promise<ReportSubscription[]> => {
    const params: Record<string, unknown> = { limit, offset };
    if (reportId !== undefined) params.report_id = reportId;
    const { data } = await api.get('/subscriptions', { params });
    return data;
  },

  /** Single lookup. Returns ``null`` on 404 (cross-user or gone) so
   *  callers can branch without try/catch around the request. */
  get: async (subscriptionId: number): Promise<ReportSubscription | null> => {
    try {
      const { data } = await api.get(`/subscriptions/${subscriptionId}`);
      return data;
    } catch (err) {
      // 404 from the router means either "no such id" or "wrong
      // owner"; both surface as not-found to the user.
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        return null;
      }
      throw err;
    }
  },

  create: async (payload: ReportSubscriptionCreate): Promise<ReportSubscription> => {
    const { data } = await api.post('/subscriptions', payload);
    return data;
  },

  update: async (
    subscriptionId: number,
    payload: ReportSubscriptionUpdate,
  ): Promise<ReportSubscription> => {
    const { data } = await api.patch(`/subscriptions/${subscriptionId}`, payload);
    return data;
  },

  delete: async (subscriptionId: number): Promise<void> => {
    await api.delete(`/subscriptions/${subscriptionId}`);
  },

  /** Pause (set ``is_active=false``) without deleting. */
  pause: async (subscriptionId: number): Promise<ReportSubscription> => {
    const { data } = await api.post(`/subscriptions/${subscriptionId}/pause`);
    return data;
  },

  /** Resume a paused subscription. */
  resume: async (subscriptionId: number): Promise<ReportSubscription> => {
    const { data } = await api.post(`/subscriptions/${subscriptionId}/resume`);
    return data;
  },
};

// ============ Report Versions ============

export const reportVersionsApi = {
  list: async (reportId: number): Promise<ReportVersionSummary[]> => {
    const { data } = await api.get(`/reports/${reportId}/versions`);
    return data;
  },
  get: async (reportId: number, versionId: number): Promise<ReportVersionResponse> => {
    const { data } = await api.get(`/reports/${reportId}/versions/${versionId}`);
    return data;
  },
  create: async (
    reportId: number,
    payload: ReportVersionCreate,
  ): Promise<ReportVersionResponse> => {
    const { data } = await api.post(`/reports/${reportId}/versions`, payload);
    return data;
  },
  restore: async (
    reportId: number,
    versionId: number,
    options: { expectedUpdatedAt?: string | null } = {},
  ): Promise<ReportVersionRestoreResponse> => {
    // A5: optimistic-lock body. ``undefined`` → field omitted from
    // payload (Pydantic default kicks in: skip the check); ``null``
    // → explicit opt-out; ISO string → lock-check on the server.
    const { data } = await api.post(`/reports/${reportId}/versions/${versionId}/restore`, {
      expected_updated_at: options.expectedUpdatedAt ?? null,
    });
    return data;
  },
  delete: async (reportId: number, versionId: number): Promise<void> => {
    await api.delete(`/reports/${reportId}/versions/${versionId}`);
  },
  diff: async (
    reportId: number,
    versionId: number,
    against: number | 'current' = 'current',
  ): Promise<ReportVersionDiff> => {
    const { data } = await api.get(`/reports/${reportId}/versions/${versionId}/diff`, {
      params: { against: against === 'current' ? 'current' : String(against) },
    });
    return data;
  },
};

// ============ Admin metrics (批 12) ============
//
// Admin-only endpoint — the page component guards the route with
// ``RequireAdmin``; the API client itself just shapes the request.
export const adminMetricsApi = {
  get: async (): Promise<AdminMetricsResponse> => {
    const { data } = await api.get('/admin/metrics');
    return data;
  },
};
