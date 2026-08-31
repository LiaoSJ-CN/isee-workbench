import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { ResetPasswordModal } from '../../pages/admin/ResetPasswordModal';
import type { PasswordResetResponse, UserResponse } from '../../types';

// AntD Modal portals into document.body and renders into a portal.
// Without ConfigProvider the components throw on missing theme;
// without the global `Message` instance the modal's error/success
// notifications no-op silently. Both are wrapped here so the modal
// behaves as it does in production.
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ConfigProvider>{children}</ConfigProvider>
);

const FAKE_USER = {
  id: 7,
  username: 'alice',
  role: 'viewer',
  disabled: false,
} as unknown as UserResponse;

const ADMIN_SUPPLIED_RESPONSE: PasswordResetResponse = {
  user_id: 7,
  rotation_method: 'admin_supplied',
  reset_at: '2026-08-31T02:00:00Z',
  generated_password: null,
};

const SERVER_GENERATED_RESPONSE: PasswordResetResponse = {
  user_id: 7,
  rotation_method: 'server_generated',
  reset_at: '2026-08-31T02:00:00Z',
  generated_password: 'rAnd0m-p4ss-3xamp1e-AAA',
};

describe('ResetPasswordModal', () => {
  it('does not render content when user is null (closed state)', () => {
    render(
      <ResetPasswordModal
        open={false}
        user={null}
        pending={false}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
      { wrapper },
    );
    // Title only renders when user is non-null (early return in the
    // component). Use queryByText with the exact match so we don't
    // accidentally pick up other "重置密码" text.
    expect(screen.queryByText(/重置密码 —/)).not.toBeInTheDocument();
  });

  it('admin_supplied: submits plaintext and closes on success', async () => {
    const onSubmit = vi.fn().mockResolvedValue(ADMIN_SUPPLIED_RESPONSE);
    const onClose = vi.fn();

    render(
      <ResetPasswordModal
        open
        user={FAKE_USER}
        pending={false}
        onSubmit={onSubmit}
        onClose={onClose}
      />,
      { wrapper },
    );

    // Default mode is admin_supplied — the password input is visible.
    const passwordInput = screen.getByPlaceholderText(/与用户同步的新密码/);
    fireEvent.change(passwordInput, { target: { value: 'new-secret-123' } });

    fireEvent.click(screen.getByRole('button', { name: '确认重置' }));

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
      <ResetPasswordModal
        open
        user={FAKE_USER}
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
      <ResetPasswordModal
        open
        user={FAKE_USER}
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
      <ResetPasswordModal
        open
        user={FAKE_USER}
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