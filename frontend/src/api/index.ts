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

const API_BASE = 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
});

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
    notificationConfig?: Record<string, unknown>
  ): Promise<SchedulerJob> => {
    const { data } = await api.post(`/scheduler/jobs/${reportId}`, null, {
      params: {
        cron_expression: cronExpression,
        notification_config: notificationConfig,
      },
    });
    return data;
  },

  deleteJob: async (reportId: number): Promise<void> => {
    await api.delete(`/scheduler/jobs/${reportId}`);
  },
};
