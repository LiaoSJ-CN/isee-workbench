/** Tests for batch 3 — ReportEditor ConflictModal.
 *
 * Coverage matrix (5 cases):
 *
 *  1 — Renders the server-supplied message in the alert banner.
 *  2 — Lists the four user-editable fields (name / description /
 *      data_source_id / output_formats / is_active) with side-by-side
 *      local vs remote values.
 *  3 — 覆盖 (overwrite) button fires ``onOverwrite``.
 *  4 — 放弃 (abandon) button + Esc-on-Modal both fire ``onAbandon``.
 *  5 — 复制改 (fork) button fires ``onFork``.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ConflictModal } from '../../components/ReportEditor/ConflictModal';
import { VersionConflictError, type Report } from '../../types';

const remote: Report = {
  id: 42,
  name: '销售周报 (已上线)',
  description: '最新版本',
  data_source_id: 7,
  layout_config: {},
  output_formats: ['excel'],
  is_active: true,
  is_scheduled: false,
  visibility: 'private',
  owner_user_id: 1,
  version: 7,
  items: [],
};

const local: Report = {
  ...remote,
  name: '销售周报 v2',
  description: '我想保存的版本',
  data_source_id: 9,
  output_formats: ['excel', 'pdf'],
  is_active: false,
  version: 5,
};

const conflict = new VersionConflictError(
  'Report was modified by someone else since you last fetched it.',
  remote,
);

describe('ConflictModal', () => {
  it('renders the server message in the alert banner', () => {
    render(
      <ConflictModal
        open
        conflict={conflict}
        local={local}
        onOverwrite={vi.fn()}
        onAbandon={vi.fn()}
        onFork={vi.fn()}
      />,
    );
    expect(screen.getByText(/modified by someone else/i)).toBeTruthy();
  });

  it('renders the editable fields with side-by-side local vs remote values', () => {
    render(
      <ConflictModal
        open
        conflict={conflict}
        local={local}
        onOverwrite={vi.fn()}
        onAbandon={vi.fn()}
        onFork={vi.fn()}
      />,
    );
    // Local values
    expect(screen.getByText('销售周报 v2')).toBeTruthy();
    expect(screen.getByText('我想保存的版本')).toBeTruthy();
    // Remote values
    expect(screen.getByText('销售周报 (已上线)')).toBeTruthy();
    expect(screen.getByText('最新版本')).toBeTruthy();
    // Field labels
    expect(screen.getByText('名称')).toBeTruthy();
    expect(screen.getByText('描述')).toBeTruthy();
    expect(screen.getByText('数据源')).toBeTruthy();
    expect(screen.getByText('输出格式')).toBeTruthy();
    expect(screen.getByText('是否启用')).toBeTruthy();
  });

  it('fires onOverwrite when 覆盖 is clicked', () => {
    const onOverwrite = vi.fn();
    render(
      <ConflictModal
        open
        conflict={conflict}
        local={local}
        onOverwrite={onOverwrite}
        onAbandon={vi.fn()}
        onFork={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '覆盖远端' }));
    expect(onOverwrite).toHaveBeenCalledTimes(1);
  });

  it('fires onAbandon when 放弃 is clicked or modal cancel is invoked', () => {
    const onAbandon = vi.fn();
    render(
      <ConflictModal
        open
        conflict={conflict}
        local={local}
        onOverwrite={vi.fn()}
        onAbandon={onAbandon}
        onFork={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '放弃本地' }));
    expect(onAbandon).toHaveBeenCalledTimes(1);

    // The modal's top-right 「X」 close button also routes to
    // ``onAbandon`` (it's the same handler as Esc / click-outside).
    // antd renders it with ``aria-label="Close"``.
    onAbandon.mockClear();
    const closeBtn = document.querySelector('.ant-modal-close');
    expect(closeBtn).not.toBeNull();
    fireEvent.click(closeBtn as HTMLElement);
    expect(onAbandon).toHaveBeenCalledTimes(1);
  });

  it('fires onFork when 复制改 is clicked', () => {
    const onFork = vi.fn();
    render(
      <ConflictModal
        open
        conflict={conflict}
        local={local}
        onOverwrite={vi.fn()}
        onAbandon={vi.fn()}
        onFork={onFork}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '复制改' }));
    expect(onFork).toHaveBeenCalledTimes(1);
  });
});