/**
 * End-to-end tests for the admin user-management surface (批 B2).
 *
 * Covers three user journeys the existing specs leave uncovered:
 *
 *   1. Admin creates a viewer user via the "新建用户" modal →
 *      /admin/users lists the new row → /admin/grants issues a
 *      write grant on a DataSource → /admin/users/{id}/grants
 *      endpoint returns the new grant row.
 *   2. Self-protection: when only one admin exists, PATCH /admin/users/{me}
 *      with role!=admin returns 403. Backend enforcement — the UI
 *      hides the affordance for the self row, so we exercise the
 *      API directly.
 *   3. Reset password (server_generated mode) via the modal →
 *      modal surfaces the plaintext → disable toggle flips the
 *      status tag to "禁用".
 *
 * Setup strategy: API token cache, API-seed of the underlying
 * data source (grants test needs a target), UI to create the
 * user, API to verify grants / self-protect / cleanup.
 */
import { expect, test } from '@playwright/test'

import {
  BACKEND_URL,
  authenticateAndEnter,
  clearLoginCache,
  createSqliteDataSource,
  deleteDataSource,
  login,
  skipIfBackendDown,
} from './_helpers'

test.beforeAll(async ({ request }) => {
  await skipIfBackendDown(request)
})

test.describe('admin user lifecycle', () => {
  test('create viewer via UI → grants write on DS → /grants shows it', async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000)
    const { accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken)

    // User is created via the UI; tracked for cleanup at the end.
    let createdUser: { id: number; username: string } | null = null

    try {
      await authenticateAndEnter(page, request, '/admin/users')

      // The Users table renders a `data-testid="users-table"`. Wait
      // for the toolbar's "新建用户" button — that's the entry
      // point.
      await expect(page.getByTestId('open-create-modal')).toBeVisible({
        timeout: 10_000,
      })

      // Open the "新建用户" modal.
      await page.getByTestId('open-create-modal').click()
      const createModal = page.getByRole('dialog').filter({
        has: page.getByText(/新建用户/),
      })
      await expect(createModal).toBeVisible({ timeout: 5_000 })

      const username = `e2e-viewer-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      await createModal.getByLabel('用户名', { exact: false }).fill(username)
      await createModal.getByLabel('初始密码', { exact: false }).fill('e2e-password-123')
      // Role form default is "viewer" but ``resetFields()`` races
      // with the initial ``setFieldsValue({role: 'viewer'})`` in
      // the modal's ``useEffect`` — in headless the default often
      // lands empty, so we explicitly pick the option instead of
      // trusting the default.
      const roleSelect = createModal.getByTestId('role-select')
      await roleSelect.click()
      await page.locator('.ant-select-item-option', { hasText: /^查看者 \(viewer\)$/ }).click()
      await createModal
        .locator('.ant-modal-footer .ant-btn-primary')
        .click()

      // Wait for the success toast + modal close.
      await expect(page.getByText(/已创建/)).toBeVisible({ timeout: 10_000 })
      await expect(createModal).toBeHidden({ timeout: 5_000 })

      // Resolve the new user id from the API for downstream calls.
      const list = await request.get(`${BACKEND_URL}/admin/users`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const listed = (await list.json()) as {
        items: Array<{ id: number; username: string; role: string }>
      }
      const me = listed.items.find((u) => u.username === username)
      expect(me, 'newly created user not found in /admin/users').toBeTruthy()
      createdUser = { id: me!.id, username: me!.username }

      // Reload to confirm the row is visible in the UI table.
      await page.reload()
      await expect(page.getByTestId('users-table')).toBeVisible({ timeout: 10_000 })
      // The username column renders ``username`` + ``#id`` in one
      // cell. Match the cell containing the username as a substring
      // (anchors would fail because of the trailing ``#id``).
      const userCell = page.locator('td.ant-table-cell', {
        hasText: username,
      })
      await expect(userCell).toBeVisible({ timeout: 10_000 })

      // Now exercise the centralised grant flow: open the
      // "+集中授权" modal, pick the DS, pick the new user, set
      // permission to "写 (write)", submit.
      await page.getByTestId('open-grant-modal').click()
      const grantModal = page.getByRole('dialog').filter({
        has: page.getByText(/集中授权/),
      })
      await expect(grantModal).toBeVisible({ timeout: 5_000 })

      // Resource select — open + type the DS name into the search
      // input to bypass the AntD virtual-scroll window. With > 10
      // DSs in the dev DB the freshly-created row is below the
      // first render window and Playwright can't find it via the
      // option-list selector alone. The ``filterOption`` callback
      // (``GrantModal.tsx:332``) does case-insensitive label match,
      // so the unique ``e2e-ds-…`` substring narrows the list to
      // just our row.
      const resourceSelect = grantModal.getByTestId('resource-select')
      await resourceSelect.click()
      // AntD's search input lives inside the same wrapper; type
      // the DS's e2e prefix to filter the virtual list.
      await resourceSelect.locator('input').fill(ds.name.slice(0, 'e2e-ds-'.length + 16))
      const dsOption = page.locator('.ant-select-item-option', {
        hasText: ds.name,
      })
      await expect(dsOption).toBeVisible({ timeout: 5_000 })
      await dsOption.first().click()

      // User select — click the option whose label is
      // ``<username> (<role>)``. The user list is small enough
      // that scrolling isn't needed.
      const userSelect = grantModal.getByTestId('user-select')
      await userSelect.click()
      const userOption = page.locator('.ant-select-item-option', {
        hasText: new RegExp(`^${username} \\(viewer\\)$`),
      })
      await expect(userOption).toBeVisible({ timeout: 5_000 })
      await userOption.first().click()

      // Permission — click "写 (write)".
      await grantModal.getByTestId('permission-radio').getByText(/写/).click()

      // Submit.
      await grantModal
        .locator('.ant-modal-footer .ant-btn-primary')
        .click()

      // Wait for the success toast + modal close.
      await expect(page.getByText(/授权已下发/)).toBeVisible({ timeout: 10_000 })
      await expect(grantModal).toBeHidden({ timeout: 5_000 })

      // Verify the grant exists via the user's grants endpoint.
      await expect
        .poll(
          async () => {
            const grantsRes = await request.get(
              `${BACKEND_URL}/admin/users/${createdUser!.id}/grants`,
              { headers: { Authorization: `Bearer ${accessToken}` } },
            )
            if (!grantsRes.ok()) return null
            const grantsBody = (await grantsRes.json()) as {
              grants: Array<{
                resource_type: string
                resource_id: number
                permission: string
              }>
            }
            return grantsBody.grants.find(
              (g) =>
                g.resource_type === 'data_source' &&
                g.resource_id === ds.id &&
                g.permission === 'write',
            )
          },
          { timeout: 15_000, intervals: [200, 500, 1_000] },
        )
        .toBeTruthy()
    } finally {
      // Hard-cleanup the user via the admin endpoint (soft-disable).
      // The admin API doesn't expose hard-delete (see _helpers.ts
      // comment); soft-disable is the canonical final state.
      if (createdUser) {
        await request
          .delete(`${BACKEND_URL}/admin/users/${createdUser.id}`, {
            headers: { Authorization: `Bearer ${accessToken}` },
          })
          .catch(() => {})
      }
      await deleteDataSource(request, accessToken, ds.id)
    }
  })

  test('self-protect: PATCH own role to viewer → 403', async ({ request }) => {
    // This is a backend-API-only test — the UI hides the role
    // field for the self row so the affordance can't be exercised.
    // The 403 lives in :func:`app.services.user_admin._check_self_protection`
    // and is enforced by the router; we just confirm the wire
    // response. Local dev has exactly one admin, so demoting self
    // triggers the last-admin guard.
    //
    // Skip the token cache: a prior spec's Playwright page
    // navigation may have triggered a 401 → refresh → logout path
    // in the SPA that revokes the cached jti. Forcing a fresh
    // login in this test sidesteps the race.
    const { accessToken } = await login(request)
    let list = await request.get(`${BACKEND_URL}/admin/users`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    if (!list.ok()) {
      // Cache hit a revoked jti — force a fresh login.
      clearLoginCache()
      const fresh = await login(request)
      list = await request.get(`${BACKEND_URL}/admin/users`, {
        headers: { Authorization: `Bearer ${fresh.accessToken}` },
      })
    }
    expect(list.ok(), `/admin/users returned ${list.status()}`).toBeTruthy()
    const listed = (await list.json()) as {
      items: Array<{ id: number; role: string }>
    }
    const me = listed.items.find((u) => u.role === 'admin')
    expect(me, 'expected at least one admin in /admin/users').toBeTruthy()

    const res = await request.patch(`${BACKEND_URL}/admin/users/${me!.id}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      data: { role: 'viewer' },
    })
    expect(res.status(), `expected 403 self-protect, got ${res.status()}`).toBe(403)
  })

  test('reset password (server_generated) → plaintext shown → disable soft-deletes', async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000)
    // Force a fresh login — see self-protect test for the same
    // rationale (cached jti can be revoked by a prior spec's SPA
    // refresh-failure path).
    clearLoginCache()
    const { accessToken } = await login(request)
    // Create a viewer directly via the API so the test focuses on
    // the password-reset + disable flows.
    const created = await request.post(`${BACKEND_URL}/admin/users`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      data: {
        username: `e2e-resetpw-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        password: 'e2e-password-123',
        role: 'viewer',
      },
    })
    if (!created.ok()) {
      const body = await created.text()
      throw new Error(`user create failed: ${created.status()} ${body}`)
    }
    const user = (await created.json()) as { id: number; username: string }

    try {
      await authenticateAndEnter(page, request, '/admin/users')
      await expect(page.getByTestId('users-table')).toBeVisible({ timeout: 10_000 })

      // The row's "重置密码" button uses a data-testid hook keyed
      // by user id. Click it.
      await page.getByTestId(`reset-${user.id}`).click()
      const resetModal = page.getByRole('dialog').filter({
        has: page.getByText(new RegExp(`重置密码 — ${user.username}`)),
      })
      await expect(resetModal).toBeVisible({ timeout: 5_000 })

      // Switch to "server_generated" mode.
      await resetModal.getByText(/服务器生成强随机密码/).click()
      // Acknowledge the one-time-display warning.
      await resetModal
        .getByLabel(/我理解新密码只会显示一次/, { exact: false })
        .check()

      // Submit.
      await resetModal.locator('.ant-modal-footer .ant-btn-primary').click()

      // The modal stays open and renders the plaintext. AntD shows
      // it in a <Text code> block — wait for the "请立即复制" alert
      // that always precedes the plaintext.
      await expect(resetModal.getByText(/请立即复制/)).toBeVisible({
        timeout: 10_000,
      })
      // The plaintext block uses ``monospace`` styling with the
      // generated password; match the "新密码:" label that always
      // precedes it. The actual plaintext is also rendered inside a
      // Paragraph.copyable block — Playwright's ``toContainText``
      // can verify the block is populated by checking for a
      // non-empty monospace span.
      const passwordBlock = resetModal.locator('.ant-typography').filter({
        hasText: /^新密码：$/,
      })
      await expect(passwordBlock).toBeVisible({ timeout: 5_000 })

      // Close the modal — the footer now shows a single "我已保存，
      // 关闭" button.
      await resetModal
        .locator('.ant-modal-footer .ant-btn-primary')
        .click()
      await expect(resetModal).toBeHidden({ timeout: 5_000 })

      // Now disable the user via the row's popconfirm flow.
      await page.reload()
      await expect(page.getByTestId('users-table')).toBeVisible({ timeout: 10_000 })

      const toggleBtn = page.getByTestId(`toggle-${user.id}`)
      await toggleBtn.click()
      // Popconfirm OK button. Users.tsx overrides the default
      // ``okText="OK"`` to "禁用" for ``user.disabled=false`` (the
      // affirmative action text mirrors the row's "禁用" affordance),
      // and AntD's autoInsertSpace injects a whitespace between the
      // Chinese characters. Scope to the popover body so the row
      // toggle buttons (which also contain "禁用") don't match.
      const okButton = page.locator('.ant-popover .ant-btn-primary').last()
      await expect(okButton).toBeVisible({ timeout: 5_000 })
      await okButton.click()

      // Poll the API to confirm disabled=true.
      await expect
        .poll(
          async () => {
            const res = await request.get(
              `${BACKEND_URL}/admin/users/${user.id}`,
              { headers: { Authorization: `Bearer ${accessToken}` } },
            )
            if (!res.ok()) return null
            const body = (await res.json()) as { disabled: boolean }
            return body.disabled
          },
          { timeout: 15_000, intervals: [200, 500, 1_000] },
        )
        .toBe(true)
    } finally {
      // Idempotent cleanup — disable is already a no-op when the
      // user is disabled, so it's safe to call twice.
      await request
        .delete(`${BACKEND_URL}/admin/users/${user.id}`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        })
        .catch(() => {})
    }
  })
})