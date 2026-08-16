import axios from 'axios';
import type {
  DataSource,
  DataSourceCreate,
  DataSourceSchema,
  QueryResult,
  Report,
  ReportCreate,
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
  SchedulerStatus,
  SchedulerJob,
} from '../types';

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
  }
);

// ============ Auth ============

export const authApi = {
  login: async (
    username: string,
    password: string
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
  me: async (): Promise<{ username: string }> => {
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
};

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

  // Report Items
  createItem: async (reportId: number, payload: ReportItemCreate): Promise<ReportItem> => {
    const { data } = await api.post(`/reports/${reportId}/items`, payload);
    return data;
  },

  updateItem: async (
    reportId: number,
    itemId: number,
    payload: ReportItemUpdate
  ): Promise<ReportItem> => {
    const { data } = await api.put(`/reports/${reportId}/items/${itemId}`, payload);
    return data;
  },

  reorderItems: async (
    reportId: number,
    items: { item_id: number; order_index: number }[]
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
    parameters?: Record<string, unknown>
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
    format: 'html' = 'html'
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
    try {
      const response = await api.get(`/reports/${reportId}/export/${format}`, {
        responseType: 'blob',
      });
      const contentType = String(response.headers['content-type'] || '');
      if (contentType.includes('application/json')) {
        const text = await response.data.text();
        let detail = text;
        try {
          const parsed = JSON.parse(text);
          detail = parsed.detail || text;
        } catch { /* use raw text */ }
        throw { response: { data: { detail } } };
      }
      const blob = new Blob([response.data]);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${filename}.${format}`;
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
          } catch { /* use raw text */ }
          throw { response: { data: { detail } } };
        }
      }
      throw err;
    }
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
    const { data } = await api.put(
      `/reports/${reportId}/parameters/${paramId}`,
      payload,
    );
    return data;
  },

  delete: async (reportId: number, paramId: number): Promise<void> => {
    await api.delete(`/reports/${reportId}/parameters/${paramId}`);
  },
};
