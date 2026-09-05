/** 批 3 — ReportEditor ConflictModal.
 *
 * Shown when ``useUpdateReport`` rejects a save with a typed
 * ``VersionConflictError`` (server returned 412 Precondition Failed).
 * Three exits, each a distinct user intent:
 *
 *  - 覆盖 (Overwrite) — refetch the latest server state and re-PUT
 *    with the new ETag. The caller's local changes win; remote
 *    changes are clobbered.
 *  - 放弃 (Abandon) — close the modal, let ``onSettled``'s
 *    invalidation refresh the cache, then sync the editor buffer to
 *    server truth. The caller's local changes are lost.
 *  - 复制改 (Fork) — navigate to ``/reports/{id}/duplicate`` which
 *    creates a private copy the caller can edit without touching the
 *    contested row. Safe by construction — neither side wins.
 *
 * Layout:
 *
 *  ┌─────────────────────────────────────────────────────────┐
 *  │ 远端已被修改                                           │
 *  │ ─────────────────────────────────────────────────────── │
 *  │ 字段       本地编辑           远端                     │
 *  │ 名称       销售周报 v2        销售周报 (已上线)        │
 *  │ 描述       …                  …                        │
 *  │ 数据源     Postgres prod       OpenGauss staging        │
 *  │ 版本       v5                  v7                       │
 *  │                                                         │
 *  │                  [复制改]  [放弃]  [覆盖]               │
 *  └─────────────────────────────────────────────────────────┘
 *
 * Why three buttons rather than two: abandoning local edits is the
 * safest default, but the user might want to keep their changes —
 * hence overwrite. Fork is the no-blame option: both edits survive
 * but in separate reports.
 */

import { Alert, Button, Modal, Space, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { ReactElement } from 'react';

import type { Report } from '../../types';
import type { VersionConflictError } from '../../types';

interface ConflictRow {
  key: string;
  field: string;
  local: string;
  remote: string;
}

export interface ConflictModalProps {
  open: boolean;
  conflict: VersionConflictError | null;
  /** The editor's local copy — what the user was about to save. */
  local: Report | null;
  onOverwrite: () => void;
  onAbandon: () => void;
  onFork: () => void;
}

const COLUMNS: ColumnsType<ConflictRow> = [
  { dataIndex: 'field', title: '字段', width: 96 },
  { dataIndex: 'local', title: '本地编辑' },
  { dataIndex: 'remote', title: '远端（当前服务器）' },
];

function short(v: unknown): string {
  if (v == null) return '∅';
  if (typeof v === 'string') return v.length > 60 ? `${v.slice(0, 57)}…` : v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

function diffRows(local: Report | null, remote: Report): ConflictRow[] {
  // The fields the user can actually edit on the 「报表配置」 tab.
  // Keep this in sync with ``handleSaveReport``'s payload in
  // ReportEditor/index.tsx.
  const fields: Array<[string, keyof Report]> = [
    ['名称', 'name'],
    ['描述', 'description'],
    ['数据源', 'data_source_id'],
    ['输出格式', 'output_formats'],
    ['是否启用', 'is_active'],
  ];
  return fields.map(([label, key]) => ({
    key,
    field: label,
    local: short(local?.[key]),
    remote: short(remote[key]),
  }));
}

export function ConflictModal({
  open,
  conflict,
  local,
  onOverwrite,
  onAbandon,
  onFork,
}: ConflictModalProps): ReactElement {
  const remote = conflict?.current ?? null;
  const rows = remote ? diffRows(local, remote) : [];

  return (
    <Modal
      title="远端已被修改"
      open={open}
      onCancel={onAbandon}
      footer={null}
      width={720}
      destroyOnClose
      // Esc = abandon too — it's the safest default.
      keyboard
    >
      <Alert
        type="warning"
        showIcon
        message={conflict?.message ?? '该报表在你保存之前已被其他人或定时任务改动。'}
        style={{ marginBottom: 16 }}
      />
      <Table<ConflictRow>
        size="small"
        pagination={false}
        dataSource={rows}
        columns={COLUMNS}
        locale={{ emptyText: '无可比较字段' }}
      />
      <div
        style={{
          marginTop: 8,
          fontSize: 12,
          color: '#666',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <span>
          本地版本:&nbsp;v{local?.version ?? '∅'} · 远端版本:&nbsp;
          v{remote?.version ?? '∅'}
        </span>
        <span>选择「复制改」会在新报表上保留双方改动。</span>
      </div>
      <div style={{ marginTop: 16, textAlign: 'right' }}>
        <Space>
          <Button onClick={onFork}>复制改</Button>
          <Button onClick={onAbandon}>放弃本地</Button>
          <Button type="primary" danger onClick={onOverwrite}>
            覆盖远端
          </Button>
        </Space>
      </div>
    </Modal>
  );
}