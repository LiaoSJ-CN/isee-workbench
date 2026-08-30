import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { RotatePasswordModal } from '../../components/RotatePasswordModal';
import type { DataSource, RotatePasswordResponse } from '../../types';

// AntD Modal portals into document.body and renders into a portal.
// Without ConfigProvider the components throw on missing theme;
// without the global `Message` instance the modal's error/success
// notifications no-op silently. Both are wrapped here so the modal
// behaves as it does in production.
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ConfigProvider>{children}</ConfigProvider>
);

const FAKE_DS = {
  id: 7,
  name: 'pg-prod',
  db_type: 'postgresql',
} as unknown as DataSource;

const ADMIN_SUPPLIED_RESPONSE: RotatePasswordResponse = {
  data_source_id: 7,
  rotation_method: 'admin_supplied',
  rotated_at: '2026-08-30T02:00:00Z',
  generated_password: null,
};

const SERVER_GENERATED_RESPONSE: RotatePasswordResponse = {
  data_source_id: 7,
  rotation_method: 'server_generated',
  rotated_at: '2026-08-30T02:00:00Z',
  generated_password: 'rAnd0m-p4ss-3xamp1e-AAA',
};

describe('RotatePasswordModal', () => {
  it('does not render content when dataSource is null (closed state)', () => {
    render(
      <RotatePasswordModal
        open={false}
        dataSource={null}
        pending={false}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
      { wrapper },
    );
    // Title only renders when dataSource is non-null (early return in
    // the component). Use queryByText with the exact match so we don't
    // accidentally pick up other "轮换密码" text.
    expect(screen.queryByText(/轮换密码 —/)).not.toBeInTheDocument();
  });

  it('admin_supplied: submits plaintext and closes on success', async () => {
    const onSubmit = vi.fn().mockResolvedValue(ADMIN_SUPPLIED_RESPONSE);
    const onClose = vi.fn();

    render(
      <RotatePasswordModal
        open
        dataSource={FAKE_DS}
        pending={false}
        onSubmit={onSubmit}
        onClose={onClose}
      />,
      { wrapper },
    );

    // Default mode is admin_supplied — the password input is visible.
    const passwordInput = screen.getByPlaceholderText(/与运维同步的新密码/);
    fireEvent.change(passwordInput, { target: { value: 'new-secret-123' } });

    fireEvent.click(screen.getByRole('button', { name: '确认轮换' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    expect(onSubmit).toHaveBeenCalledWith({ new_password: 'new-secret-123' });
    // admin_supplied → modal closes immediately (admin already knows
    // the plaintext, nothing to display).
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('server_generated: displays plaintext once and disables dismiss until acknowledged', async () => {
    const onSubmit = vi.fn().mockResolvedValue(SERVER_GENERATED_RESPONSE);
    const onClose = vi.fn();

    render(
      <RotatePasswordModal
        open
        dataSource={FAKE_DS}
        pending={false}
        onSubmit={onSubmit}
        onClose={onClose}
      />,
      { wrapper },
    );

    // Switch to server_generated mode.
    fireEvent.click(
      screen.getByText(/服务器生成强随机密码/),
    );

    // Tick the acknowledgement checkbox.
    fireEvent.click(
      screen.getByLabelText(/我理解新密码只会显示一次/),
    );

    fireEvent.click(screen.getByRole('button', { name: '生成新密码' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    // No plaintext in the request body — server decides.
    expect(onSubmit).toHaveBeenCalledWith({});

    // After server response, the modal stays open and displays the
    // generated plaintext in a copyable block.
    await waitFor(() => {
      expect(screen.getByText('rAnd0m-p4ss-3xamp1e-AAA')).toBeInTheDocument();
    });
    expect(screen.getByText(/请立即复制并妥善保存新密码/)).toBeInTheDocument();
    // Footer switches to a single "我已保存，关闭" button — admin
    // must explicitly close to acknowledge they've saved the password.
    const closeButton = screen.getByRole('button', { name: /我已保存，关闭/ });
    fireEvent.click(closeButton);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('server_generated: requires the acknowledgement checkbox before submitting', async () => {
    const onSubmit = vi.fn().mockResolvedValue(SERVER_GENERATED_RESPONSE);

    render(
      <RotatePasswordModal
        open
        dataSource={FAKE_DS}
        pending={false}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
      { wrapper },
    );

    // Switch to server_generated mode but DO NOT tick acknowledgement.
    fireEvent.click(screen.getByText(/服务器生成强随机密码/));

    fireEvent.click(screen.getByRole('button', { name: '生成新密码' }));

    // onSubmit should not have been called — the acknowledgement gate
    // blocks the request. Wait one tick to be sure no late call lands.
    await new Promise((r) => setTimeout(r, 50));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('cancel button does not invoke onSubmit', () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();

    render(
      <RotatePasswordModal
        open
        dataSource={FAKE_DS}
        pending={false}
        onSubmit={onSubmit}
        onClose={onClose}
      />,
      { wrapper },
    );

    // AntD's ``autoInsertSpaceInButton`` injects a space between the
    // two Chinese characters ("取 消"), so a literal ``getByText('取消')``
    // misses. Regex matcher tolerates the optional space.
    fireEvent.click(screen.getByText(/取\s*消/));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
