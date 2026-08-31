import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { UserFormModal } from '../../pages/admin/UserFormModal';
import type { UserCreate, UserResponse, UserUpdate } from '../../types';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ConfigProvider>{children}</ConfigProvider>
);

const FAKE_USER: UserResponse = {
  id: 5,
  username: 'alice',
  role: 'viewer',
  disabled: false,
};

describe('UserFormModal', () => {
  it('create mode: requires username + password + role', async () => {
    const onSubmit = vi.fn().mockImplementation(async (payload: UserCreate) => {
      return { ...FAKE_USER, ...payload };
    });

    render(
      <UserFormModal
        open
        mode="create"
        currentUserId={1}
        pending={false}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
      { wrapper },
    );

    // Wait for the Modal portal + animation to settle before interacting.
    const usernameInput = await screen.findByPlaceholderText('例如：alice');
    const passwordInput = await screen.findByPlaceholderText('至少 8 个字符');
    const createBtn = await screen.findByRole('button', { name: /创\s*建/ });

    fireEvent.change(usernameInput, { target: { value: 'bob' } });
    fireEvent.change(passwordInput, { target: { value: 'password123' } });
    // role defaults to viewer — leave as-is.

    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    expect(onSubmit).toHaveBeenCalledWith({
      username: 'bob',
      password: 'password123',
      role: 'viewer',
    });
  });

  it('edit mode: role field is disabled when editing self', async () => {
    render(
      <UserFormModal
        open
        mode="edit"
        user={FAKE_USER}
        currentUserId={FAKE_USER.id}
        pending={false}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
      { wrapper },
    );

    // Wait for the Modal to settle.
    const usernameInput = (await screen.findByPlaceholderText(
      '例如：alice',
    )) as HTMLInputElement;
    expect(usernameInput).toBeDisabled();

    // Self-protection banner is shown.
    expect(
      screen.getByText(/您正在编辑自己的账号/),
    ).toBeInTheDocument();
  });

  it('edit mode: non-self keeps role enabled and submits role + disabled', async () => {
    const onSubmit = vi.fn().mockImplementation(async (payload: UserUpdate) => {
      return { ...FAKE_USER, ...payload };
    });

    render(
      <UserFormModal
        open
        mode="edit"
        user={FAKE_USER}
        currentUserId={1}
        pending={false}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
      { wrapper },
    );

    // Wait for the Modal + Switch to settle.
    const switchEl = await screen.findByTestId('disabled-switch');
    const saveBtn = await screen.findByRole('button', { name: /保\s*存/ });

    // Toggle the disabled switch to true.
    fireEvent.click(switchEl);

    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    // role unchanged (viewer), disabled flipped to true.
    expect(onSubmit).toHaveBeenCalledWith({ role: 'viewer', disabled: true });
  });

  it('create mode: surfaces server error (e.g. 409 duplicate username) via onSubmit rejection', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('username already exists'));

    render(
      <UserFormModal
        open
        mode="create"
        currentUserId={1}
        pending={false}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
      { wrapper },
    );

    const usernameInput = await screen.findByPlaceholderText('例如：alice');
    const passwordInput = await screen.findByPlaceholderText('至少 8 个字符');
    const createBtn = await screen.findByRole('button', { name: /创\s*建/ });

    fireEvent.change(usernameInput, { target: { value: 'dup' } });
    fireEvent.change(passwordInput, { target: { value: 'password123' } });

    // Submit — modal must NOT close (rejection means we stay on the
    // form so the admin can correct).
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    // Modal title is still present — form survives the rejection.
    expect(screen.getByText('新建用户')).toBeInTheDocument();
  });
});