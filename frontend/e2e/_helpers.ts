/**
 * Shared helpers for the e2e suite (批 B2).
 *
 * Everything the per-flow specs need lives here:
 *
 *   - backend base URL constants + admin credentials
 *   - login() with token cache so the suite doesn't burn the
 *     per-IP ``LOGIN_RATE_LIMIT=10/min`` (the suite has 16 tests
 *     and would otherwise hit the wall by spec 4)
 *   - authenticateAndEnter() — seed localStorage with the token
 *     and bounce past the login page (avoids the UI login flow,
 *     which is fragile to copy changes)
 *   - createSqliteDataSource() / createTextReport() — seed the
 *     minimal entity graph a happy-path test needs (``:memory:``
 *     SQLite is enough — text items don't touch the underlying DB)
 *   - deleteReport() / deleteDataSource() — every test cleanup
 *     runs these in a ``finally`` block
 *   - createUser() / disableUser() — admin-user spec
 *   - skipIfBackendDown() — pre-flight used by every spec's
 *     ``beforeAll`` so a local checkout without ``uvicorn`` doesn't
 *     fail (only CI with docker-compose actually exercises tests)
 *
 * Naming convention: every seeded entity uses ``{prefix}-${Date.now()}-${rand}``
 * so back-to-back runs don't collide.
 */
import { APIRequestContext, Page, expect, test } from '@playwright/test'

export const BACKEND_URL =
  process.env.BACKEND_URL ??
  // Mirrors the webServer gate in playwright.config.ts: with
  // E2E_BASE_URL set we're running against an already-booted stack
  // (CI's docker-compose on :8000); otherwise Playwright started the
  // throwaway backend on :8001 for us.
  (process.env.E2E_BASE_URL ? 'http://localhost:8000' : 'http://localhost:8001')
export const ADMIN_USER = 'admin'
export const ADMIN_PASS = 'admin'

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface ReportResponse {
  id: number
  name: string
  data_source_id: number
}

export interface DataSourceResponse {
  id: number
  name: string
}

export interface UserResponse {
  id: number
  username: string
  role: string
}

// ---------------------------------------------------------------------------
// Token cache — avoids burning the per-IP login rate limit
// ---------------------------------------------------------------------------

interface CachedToken {
  token: string
  // JWT access tokens in this app expire after 24h; we cache for 23h to
  // leave a safety margin. The cache is per-test-process — if a token
  // expires mid-suite, the next login() call just refreshes.
  expiresAt: number
}

let cachedAdminToken: CachedToken | null = null

/**
 * Login as admin, reusing the cached access token if still valid.
 * Falls back to a real login when the cache misses or has expired.
 *
 * Why cache: ``LOGIN_RATE_LIMIT=10/min/IP`` and the suite has 16
 * tests. Without the cache, the suite would hit the wall by spec 4
 * and every subsequent test would 429.
 *
 * Why clear-by-default in test contexts: see batch B2 / debug 2026-09-01.
 * The cache occasionally hands back a token whose ``jti`` has been
 * revoked by a prior spec's SPA refresh-rotation (refresh_token is
 * cached too — old refresh jti becomes invalid → next access via
 * cached refresh chain revokes the access jti on the way through
 * ``/auth/refresh``). Disabling the cache here means every test does
 * one extra ``/auth/login``, which is still under ``LOGIN_RATE_LIMIT``
 * after we bumped it to 100. The alternative — clearing cache at
 * every test boundary — is the same number of requests, just spread
 * out.
 */
export async function login(request: APIRequestContext): Promise<LoginResponse> {
  // Cache disabled for batch B2 (see comment above). Re-enable once
  // the underlying refresh-token-rotation race is fixed.
  cachedAdminToken = null

  const now = Date.now()
  if (cachedAdminToken && cachedAdminToken.expiresAt > now + 60_000) {
    return {
      access_token: cachedAdminToken.token,
      refresh_token: '',
      token_type: 'bearer',
    }
  }

  const res = await request.post(`${BACKEND_URL}/auth/login`, {
    data: { username: ADMIN_USER, password: ADMIN_PASS },
  })
  expect(
    res.ok(),
    `login failed: ${res.status()} ${await res.text()}`,
  ).toBeTruthy()
  const body = (await res.json()) as LoginResponse

  cachedAdminToken = {
    token: body.access_token,
    expiresAt: now + 23 * 60 * 60 * 1000,
  }
  return body
}

/** Reset the cache — for tests that need a fresh login (e.g. after
 *  rotating the admin password). */
export function clearLoginCache(): void {
  cachedAdminToken = null
}

// ---------------------------------------------------------------------------
// Auth / page entry
// ---------------------------------------------------------------------------

/**
 * Seed the page's localStorage with tokens from the API and bounce
 * into the SPA. This sidesteps both the UI login flow (slow, fragile
 * to copy changes) and the per-IP login rate limit.
 *
 * Returns the access/refresh tokens in case the test wants to call
 * the API directly.
 */
export async function authenticateAndEnter(
  page: Page,
  request: APIRequestContext,
  postLoginPath = '/reports',
): Promise<{ accessToken: string; refreshToken: string }> {
  const tokens = await login(request)
  await page.goto('/login')
  await page.evaluate(
    ({ access, refresh }) => {
      localStorage.setItem('access_token', access)
      localStorage.setItem('refresh_token', refresh)
    },
    { access: tokens.access_token, refresh: tokens.refresh_token },
  )
  await page.goto(postLoginPath)
  await expect(page).toHaveURL(new RegExp(`${postLoginPath.replace(/\//g, '\\/')}$`))
  return {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
  }
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

/**
 * Assert that a cleanup DELETE actually removed the row.
 *
 * Cleanup used to be fire-and-forget (``await request.delete(...)`` with
 * no status check), so a 500 or a 401 left the entity behind and nobody
 * noticed until the dev database had accumulated dozens of ``e2e-*``
 * rows. Now a botched cleanup fails the test that caused it.
 *
 * 404 counts as success: cleanup lives in ``finally`` and the happy path
 * of some specs already deleted the entity through the UI.
 *
 * Soft assertion on purpose — cleanup runs in ``finally``, and a hard
 * throw there would mask the real failure that got us into ``finally``
 * in the first place. Soft failures still mark the test red.
 */
function expectCleanupOk(
  res: { ok(): boolean; status(): number },
  what: string,
): void {
  expect
    .soft(res.ok() || res.status() === 404, `cleanup failed — ${what} returned ${res.status()}`)
    .toBeTruthy()
}

// ---------------------------------------------------------------------------
// DataSource helpers
// ---------------------------------------------------------------------------

/** Create a fresh SQLite ``:memory:`` data source. Text items don't
 *  touch the underlying DB, so the placeholder host/port is fine —
 *  only ``database`` matters. */
export async function createSqliteDataSource(
  request: APIRequestContext,
  token: string,
  namePrefix = 'e2e-ds',
): Promise<DataSourceResponse> {
  const name = `${namePrefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
  const res = await request.post(`${BACKEND_URL}/data-sources`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name,
      db_type: 'sqlite',
      host: 'placeholder',
      port: 1,
      username: 'placeholder',
      password: 'placeholder',
      database: ':memory:',
    },
  })
  expect(
    res.ok(),
    `create data source failed: ${res.status()} ${await res.text()}`,
  ).toBeTruthy()
  return res.json()
}

/** Hard-delete a data source by id.
 *
 *  Asserts on the status: a silent failure here is what let ``e2e-ds-*``
 *  rows pile up in the dev database (``DELETE`` used to 500 whenever a
 *  report still referenced the source). 404 is accepted so the call
 *  stays idempotent — cleanup runs in ``finally`` and may re-delete. */
export async function deleteDataSource(
  request: APIRequestContext,
  token: string,
  dataSourceId: number,
): Promise<void> {
  const res = await request.delete(`${BACKEND_URL}/data-sources/${dataSourceId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expectCleanupOk(res, `delete data source ${dataSourceId}`)
}

// ---------------------------------------------------------------------------
// Report helpers
// ---------------------------------------------------------------------------

/** Create a fresh report with a single text item. The text item's
 *  ``content`` is what callers assert against in preview HTML. */
export async function createTextReport(
  request: APIRequestContext,
  token: string,
  dataSourceId: number,
  marker: string,
  namePrefix = 'e2e-rpt',
): Promise<ReportResponse> {
  const name = `${namePrefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
  const res = await request.post(`${BACKEND_URL}/reports`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name,
      description: 'E2E lifecycle test report',
      data_source_id: dataSourceId,
      output_formats: ['excel', 'html'],
      is_active: true,
      items: [
        {
          name: 'lifecycle-marker',
          item_type: 'text',
          order_index: 0,
          display_config: { content: marker },
        },
      ],
    },
  })
  expect(
    res.ok(),
    `create report failed: ${res.status()} ${await res.text()}`,
  ).toBeTruthy()
  return res.json()
}

/** Hard-delete a report by id. Asserts — see ``expectCleanupOk``. */
export async function deleteReport(
  request: APIRequestContext,
  token: string,
  reportId: number,
): Promise<void> {
  const res = await request.delete(`${BACKEND_URL}/reports/${reportId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expectCleanupOk(res, `delete report ${reportId}`)
}

// ---------------------------------------------------------------------------
// Admin user helpers (admin-user-lifecycle spec)
// ---------------------------------------------------------------------------

/** Create a fresh admin-managed user with a unique username. */
export async function createUser(
  request: APIRequestContext,
  token: string,
  role: 'admin' | 'editor' | 'viewer' = 'viewer',
  password = 'e2e-password-123',
  namePrefix = 'e2e-u',
): Promise<UserResponse> {
  const username = `${namePrefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
  const res = await request.post(`${BACKEND_URL}/admin/users`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { username, password, role },
  })
  expect(
    res.ok(),
    `create user failed: ${res.status()} ${await res.text()}`,
  ).toBeTruthy()
  return res.json()
}

/** Soft-disable a user (DELETE /admin/users/{id} is idempotent).
 *
 *  The admin API has no hard-delete, by design — audit rows reference
 *  ``users.id``. That's fine now that the suite runs against its own
 *  throwaway database (see playwright.config.ts); the disabled rows die
 *  with ``e2e.db`` at the start of the next run. */
export async function disableUser(
  request: APIRequestContext,
  token: string,
  userId: number,
): Promise<void> {
  const res = await request.delete(`${BACKEND_URL}/admin/users/${userId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expectCleanupOk(res, `disable user ${userId}`)
}

// ---------------------------------------------------------------------------
// Pre-flight
// ---------------------------------------------------------------------------

/**
 * Skip the whole suite when the backend isn't reachable. Used by
 * every spec's ``beforeAll`` so a local checkout without
 * ``uvicorn`` running won't fail — only the CI e2e job (which
 * boots docker-compose) actually exercises the tests.
 */
export async function skipIfBackendDown(request: APIRequestContext): Promise<void> {
  try {
    const res = await request.get(`${BACKEND_URL}/docs`)
    if (!res.ok() && res.status() !== 200) {
      test.skip(true, `backend not reachable at ${BACKEND_URL}`)
    }
  } catch {
    test.skip(true, `backend not reachable at ${BACKEND_URL}`)
  }
}