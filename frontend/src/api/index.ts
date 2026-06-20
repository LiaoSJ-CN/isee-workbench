import axios from 'axios';
import type {
  DataSource,
  DataSourceCreate,
  Report,
  ReportCreate,
  ReportUpdate,
  ReportItem,
  ReportItemCreate,
  ReportItemUpdate,
  ReportGenerateResponse,
  SchedulerStatus,
  SchedulerJob,
} from '../types';

export const API_BASE = 'http://localhost:8000';
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
            refreshing = axios
              .post(`${API_BASE}/auth/refresh`, { refresh_token: refreshToken })
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
    format: 'html' | 'json' = 'html'
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

  getExportUrl: (reportId: number, format: 'excel' | 'html'): string => {
    return `${API_BASE}/reports/${reportId}/export/${format}`;
  },

  download: async (reportId: number, format: 'excel' | 'html', filename: string): Promise<void> => {
    const response = await fetch(`${API_BASE}/reports/${reportId}/export/${format}`);
    if (!response.ok) {
      throw new Error('下载失败');
    }
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${filename}.${format}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
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
    notificationConfig?: Record<string, unknown> | null
  ): Promise<SchedulerJob> => {
    const body = {
      report_id: reportId,
      cron_expression: cronExpression,
      schedule_description: scheduleDescription ?? null,
      notification_config: notificationConfig ?? {},
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
  query: async (
    dataSourceId: number,
    sql: string
  ): Promise<{
    success: boolean;
    columns: string[];
    rows: Record<string, unknown>[];
    row_count: number;
    error?: string;
  }> => {
    const { data } = await api.post('/explorer/query', {
      data_source_id: dataSourceId,
      sql: sql,
    });
    return data;
  },
};
