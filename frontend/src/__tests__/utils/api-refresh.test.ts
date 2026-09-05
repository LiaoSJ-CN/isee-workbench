/**
 * Regression tests for the JWT refresh interceptor in
 * ``frontend/src/api/index.ts``.
 *
 * Background (post-批-e2e-isolation-CS, 94fbbbb): the SPA's 401 →
 * /auth/refresh → retry chain only persisted the new ``access_token``
 * to localStorage and dropped the new ``refresh_token``. The backend
 * rotates refresh jtis on every successful refresh and rejects replays
 * of the prior jti via the ``revoked_jti`` deny-list, so the next
 * 401 → refresh cycle posted a revoked jti and the SPA bounced itself
 * to ``/login`` even though the rotated cookie held a valid refresh.
 * Symptom: any user logged in for ~24h gets mysteriously logged out.
 *
 * These tests pin down the four invariants the fix has to preserve:
 *
 *   1. ``/auth/refresh`` updates BOTH tokens in localStorage.
 *   2. Concurrent 401s share a single ``/auth/refresh`` call.
 *   3. ``/auth/refresh`` itself 401-ing clears both tokens (fail path).
 *   4. ``/auth/refresh`` 401 must not trigger another refresh (no loop).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// ---- axios mock ----
//
// We mock ``axios`` at the module level. The interceptor under test
// registers itself at module-load via ``axios.create(...).interceptors
// .response.use(onFulfilled, onRejected)``. We capture the rejected-
// response handler so each test can drive a 401 through it directly,
// and we route ``post`` / ``get`` calls through a controllable
// per-test mock queue.
//
// Note: ``api(original)`` in the interceptor relies on axios's
// ``AxiosInstance`` being callable (it dispatches by HTTP method from
// the config). The mock has to be callable too — hence the function
// wrapper below.

let rejectedHandler: ((err: unknown) => unknown) | null = null
const mockPost = vi.fn()
const mockGet = vi.fn()

function buildInstance() {
  // Callable so ``api(original)`` works (real axios dispatches by
  // method). Reads ``original.method``; defaults to GET. The
  // ``Object.assign`` shape lets TypeScript infer a proper
  // intersection of the callable signature and the extra property
  // bag (``interceptors`` / ``post`` / ``get``), without resorting to
  // ``any``. A bare ``Record<string, unknown>`` annotation rejects
  // the function expression because functions have no index
  // signature, and a hand-written intersection cast hits the same
  // wall.
  const instance = Object.assign(
    function (config: { method?: string; url?: string }) {
      const method = (config.method ?? 'get').toLowerCase()
      if (method === 'get') return mockGet(config)
      if (method === 'post') return mockPost(config)
      throw new Error(`mock: unsupported method ${method}`)
    },
    {
      interceptors: {
        request: {
          use: vi.fn(),
        },
        response: {
          use: (_onFulfilled: unknown, onRejected: (err: unknown) => unknown) => {
            rejectedHandler = onRejected
          },
        },
      },
      post: (cfg: unknown, body?: unknown) => mockPost(cfg, body),
      get: (cfg: unknown) => mockGet(cfg),
    },
  )
  return instance
}

vi.mock('axios', () => ({
  default: { create: () => buildInstance() },
  create: () => buildInstance(),
}))

let registered = false
async function ensureRegistered() {
  if (registered) return
  await import('../../api')
  registered = true
}

beforeEach(async () => {
  localStorage.clear()
  localStorage.setItem('access_token', 'A0')
  localStorage.setItem('refresh_token', 'R0')
  mockPost.mockReset()
  mockGet.mockReset()
  window.history.pushState({}, '', '/reports')
  await ensureRegistered()
})

afterEach(() => {
  vi.clearAllMocks()
})

interface FakeAxiosError {
  response: { status: number; data?: unknown }
  config: { url: string; _retry?: boolean; headers: Record<string, string> }
}

async function trigger401(url: string): Promise<unknown> {
  if (!rejectedHandler) throw new Error('response interceptor not registered')
  const err: FakeAxiosError = {
    response: { status: 401, data: { detail: 'expired' } },
    config: { url, _retry: false, headers: {} },
  }
  // The interceptor's failure path calls ``window.location.href =
  // '/login'`` which throws under happy-dom (read-only). Swallow the
  // resulting rejection here so the test can assert on side-effects
  // without the throw masking them.
  try {
    return await rejectedHandler(err)
  } catch {
    return undefined
  }
}

describe('SPA JWT refresh interceptor (api/index.ts)', () => {
  it('persists BOTH rotated tokens after /auth/refresh', async () => {
    mockPost.mockResolvedValue({
      data: { access_token: 'A1', refresh_token: 'R1', token_type: 'bearer' },
    })
    mockGet.mockResolvedValue({ data: { ok: true } })

    await trigger401('/anything')

    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(mockPost).toHaveBeenCalledWith('/auth/refresh', {
      refresh_token: 'R0',
    })
    expect(localStorage.getItem('access_token')).toBe('A1')
    // The regression marker — without the fix, REFRESH_KEY stays at R0
    // and the next refresh posts a revoked jti.
    expect(localStorage.getItem('refresh_token')).toBe('R1')
    // Retry GET went out with the new bearer token.
    expect(mockGet).toHaveBeenCalledTimes(1)
    const retryConfig = mockGet.mock.calls[0][0] as { headers: Record<string, string> }
    expect(retryConfig.headers.Authorization).toBe('Bearer A1')
  })

  it('dedupes concurrent refresh calls into a single /auth/refresh POST', async () => {
    mockPost.mockResolvedValue({
      data: { access_token: 'A1', refresh_token: 'R1', token_type: 'bearer' },
    })
    mockGet.mockResolvedValue({ data: { ok: true } })

    await Promise.all([trigger401('/a'), trigger401('/b')])

    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('access_token')).toBe('A1')
    expect(localStorage.getItem('refresh_token')).toBe('R1')
  })

  it('clears both tokens when /auth/refresh itself fails', async () => {
    mockPost.mockRejectedValue({
      response: { status: 401, data: { detail: 'Refresh token has been revoked' } },
      config: { url: '/auth/refresh', headers: {} },
    })

    await trigger401('/x')

    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
  })

  it('does not trigger another refresh when /auth/refresh itself 401s (no loop)', async () => {
    mockPost.mockResolvedValue({ data: { ok: true } })

    await trigger401('/auth/refresh')

    // The interceptor short-circuits on `isRefreshCall`; no POST to
    // /auth/refresh should fire for a /auth/refresh request.
    expect(mockPost).not.toHaveBeenCalled()
  })
})
