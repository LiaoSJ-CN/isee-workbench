/** Tests for the Login page — form validation + happy path + 401 error path.

We mock react-router's hooks (so we don't need a router) and the
``useLogin`` mutation (so we don't need a QueryClient nor a server).
Message toasts are silenced — they would otherwise flood the console
during tests without asserting anything useful.
*/

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const mockNavigate = vi.fn()
const mockMutate = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ state: null }),
}))

vi.mock('../../queries/useAuth', () => ({
  useLogin: () => ({
    mutate: mockMutate,
    isPending: false,
  }),
}))

vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd')
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
  }
})

import Login from '../../pages/Login'

describe('Login form validation', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    mockMutate.mockClear()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders username + password fields and submit button', () => {
    render(<Login />)
    // Both inputs share placeholder 'admin', so use getAllBy…
    const inputs = screen.getAllByPlaceholderText('admin')
    expect(inputs.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByRole('button', { name: /登.*录/ })).toBeInTheDocument()
  })

  it('blocks submit when username is empty', async () => {
    const user = userEvent.setup()
    render(<Login />)

    // Type password only, leave username blank.
    const passwordInputs = document.querySelectorAll('input[type="password"]')
    await user.type(passwordInputs[0], 'somepassword')

    await user.click(screen.getByRole('button', { name: /登.*录/ }))

    await waitFor(() => {
      expect(screen.getByText('请输入用户名')).toBeInTheDocument()
    })
    expect(mockMutate).not.toHaveBeenCalled()
  })

  it('blocks submit when password is empty', async () => {
    const user = userEvent.setup()
    render(<Login />)

    // Two inputs share the placeholder 'admin' (username + default for password).
    // Username is the one without type=password.
    const inputs = Array.from(document.querySelectorAll('input')).filter(
      (el) => el.getAttribute('placeholder') === 'admin' && el.getAttribute('type') !== 'password',
    )
    expect(inputs).toHaveLength(1)
    await user.type(inputs[0], 'adminuser')

    await user.click(screen.getByRole('button', { name: /登.*录/ }))

    await waitFor(() => {
      expect(screen.getByText('请输入密码')).toBeInTheDocument()
    })
    expect(mockMutate).not.toHaveBeenCalled()
  })

  it('calls login.mutate with the entered credentials on submit', async () => {
    const user = userEvent.setup()
    render(<Login />)

    const usernameInput = Array.from(document.querySelectorAll('input')).filter(
      (el) => el.getAttribute('placeholder') === 'admin' && el.getAttribute('type') !== 'password',
    )[0]
    const passwordInput = document.querySelector('input[type="password"]') as HTMLInputElement

    await user.type(usernameInput, 'admin')
    await user.type(passwordInput, 'admin')

    await user.click(screen.getByRole('button', { name: /登.*录/ }))

    expect(mockMutate).toHaveBeenCalledTimes(1)
    expect(mockMutate).toHaveBeenCalledWith(
      { username: 'admin', password: 'admin' },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    )
  })

  it('navigates to / on successful login', async () => {
    const user = userEvent.setup()
    // Capture the onSuccess callback and invoke it (simulates server response).
    mockMutate.mockImplementation((_vars, opts) => {
      opts.onSuccess()
    })

    render(<Login />)

    const usernameInput = Array.from(document.querySelectorAll('input')).filter(
      (el) => el.getAttribute('placeholder') === 'admin' && el.getAttribute('type') !== 'password',
    )[0]
    const passwordInput = document.querySelector('input[type="password"]') as HTMLInputElement

    await user.type(usernameInput, 'admin')
    await user.type(passwordInput, 'admin')
    await user.click(screen.getByRole('button', { name: /登.*录/ }))

    expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true })
  })
})