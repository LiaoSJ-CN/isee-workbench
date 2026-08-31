/**
 * End-to-end tests for the report subscription lifecycle (批 B2).
 *
 * Covers the user journeys the existing smoke + report-lifecycle
 * specs leave uncovered:
 *
 *   1. Open ReportList → click a row's "更多 / 订阅" → fill the
 *      SubscriptionModal (cron + email) → /my-subscriptions shows
 *      the new row bound to the chosen report.
 *   2. With a subscription already in place, /my-subscriptions
 *      reflects the active/paused state correctly. The state
 *      transitions themselves are driven via the underlying API
 *      (POST /subscriptions/{id}/pause, /resume) for the same
 *      reason the scheduler spec drives pause/resume via the API:
 *      the row's pause/resume action buttons are AntD
 *      ``<Button type="link" icon={...}>`` whose click handler
 *      stalls intermittently under Playwright's headless click
 *      model. The DELETE path on /my-subscriptions uses the same
 *      Popconfirm that the data-source / scheduler specs exercise
 *      successfully — that flow is reliable.
 *
 * Setup strategy mirrors the other B2 specs: API token cache,
 * API-seed of the underlying data source + report, then the test
 * drives the UI affordance the user would click.
 */
import { expect, test } from '@playwright/test'

import {
  BACKEND_URL,
  authenticateAndEnter,
  createSqliteDataSource,
  createTextReport,
  deleteDataSource,
  deleteReport,
  login,
  skipIfBackendDown,
} from './_helpers'

test.beforeAll(async ({ request }) => {
  await skipIfBackendDown(request)
})

test.describe('subscription lifecycle', () => {
  test('subscribe via row dropdown → /my-subscriptions shows it', async ({
    page,
    request,
  }) => {
    const { accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken)
    const report = await createTextReport(request, accessToken, ds.id, 'e2e-sub')

    try {
      await authenticateAndEnter(page, request, '/reports')

      // Bump page size so the seeded row isn't pushed to page 2
      // by the Ant-D default of 10.
      await expect(page.locator('.ant-table-row').first()).toBeVisible({
        timeout: 10_000,
      })
      await page.locator('.ant-pagination-options-size-changer').first().click()
      await page.getByText('100 / page').click()

      const row = page
        .locator('tr')
        .filter({
          has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${report.name}$`) }),
        })
      await expect(row).toBeVisible({ timeout: 10_000 })

      // Click the row's "更多" dropdown → "订阅" menu item. Scope
      // to ``.ant-dropdown-menu-item`` so the global header menu's
      // "我的订阅" entry doesn't also match the regex.
      await row.getByRole('button', { name: /更\s*多/ }).click()
      const subscribeMenuItem = page.locator('.ant-dropdown-menu-item', {
        hasText: /订\s*阅/,
      })
      await expect(subscribeMenuItem).toBeVisible({ timeout: 5_000 })
      await subscribeMenuItem.click()

      // The SubscriptionModal opens. The cron field is pre-filled
      // with ``0 9 * * * 2026`` and notification_type with
      // ``email``. We only need to type the recipient + subject —
      // both are required by the form validation.
      const modal = page.getByRole('dialog').filter({
        has: page.getByText(/订阅报表/),
      })
      await expect(modal).toBeVisible({ timeout: 5_000 })

      await modal.getByLabel('收件人', { exact: false }).fill('e2e@example.com')
      await modal.getByLabel('邮件主题', { exact: false }).fill('e2e sub test')

      // AntD modal OK button — class selector avoids the
      // autoInsertSpace text-match flake.
      await modal.locator('.ant-modal-footer .ant-btn-primary').click()

      // Wait for the success toast then for the modal to close —
      // the success path runs ``onClose()`` only after the mutation
      // resolves.
      await expect(page.getByText(/订阅已创建/)).toBeVisible({ timeout: 10_000 })
      await expect(modal).toBeHidden({ timeout: 5_000 })

      // Navigate to /my-subscriptions and verify the new row.
      await page.goto('/my-subscriptions')
      await expect(page.getByRole('heading', { name: /我的订阅/ })).toBeVisible({
        timeout: 10_000,
      })

      // The subscription's "报表" column shows the report name
      // (resolved client-side from /reports). Match the exact name
      // cell so we don't pick up unrelated subscriptions left from
      // earlier runs.
      const subRow = page
        .locator('tr')
        .filter({
          has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${report.name}$`) }),
        })
      await expect(subRow).toBeVisible({ timeout: 10_000 })
      // Status column renders "运行中" (running) since the new
      // subscription is active by default. Cron column renders
      // ``0 9 * * * 2026`` (the form pre-fill).
      await expect(subRow.getByText(/^运行中$/, { exact: true })).toBeVisible()
      await expect(subRow.getByText('0 9 * * * 2026', { exact: true })).toBeVisible()

      // Cleanup: the subscription row. We list and delete by id so
      // we don't depend on the DOM after the page snapshot above.
      const list = await request.get(`${BACKEND_URL}/subscriptions`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const items = (await list.json()) as Array<{
        id: number
        report_id: number
      }>
      for (const it of items) {
        if (it.report_id === report.id) {
          await request.delete(`${BACKEND_URL}/subscriptions/${it.id}`, {
            headers: { Authorization: `Bearer ${accessToken}` },
          })
        }
      }
    } finally {
      await deleteReport(request, accessToken, report.id)
      await deleteDataSource(request, accessToken, ds.id)
    }
  })

  test('pause + resume subscription flips status between 运行中 and 已暂停', async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000)
    const { accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken)
    const report = await createTextReport(request, accessToken, ds.id, 'e2e-sub')

    // Seed the subscription directly via the API so the test
    // focuses on the page's render of active/paused states. (The
    // UI path that exercises the modal is covered by the test
    // above.)
    const createRes = await request.post(`${BACKEND_URL}/subscriptions`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      data: {
        report_id: report.id,
        cron_expression: '0 9 * * * 2026',
        parameters: {},
        notification_config: null,
      },
    })
    expect(createRes.ok(), `subscription create failed: ${createRes.status()}`).toBeTruthy()
    const subscription = (await createRes.json()) as { id: number }

    try {
      await authenticateAndEnter(page, request, '/my-subscriptions')
      await expect(page.getByRole('heading', { name: /我的订阅/ })).toBeVisible({
        timeout: 10_000,
      })

      // Find the seeded subscription row by its report name.
      const row = page
        .locator('tr')
        .filter({
          has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${report.name}$`) }),
        })
      await expect(row).toBeVisible({ timeout: 10_000 })
      await expect(row.getByText(/^运行中$/, { exact: true })).toBeVisible({
        timeout: 10_000,
      })

      // Flip to paused via API. The row's pause button click is
      // documented as flaky (icon-wrapped ``<Button type="link">``)
      // — same root cause as the scheduler pause/resume spec.
      await request.post(`${BACKEND_URL}/subscriptions/${subscription.id}/pause`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })

      // Poll until the backend reflects the change, then reload
      // so the table refetches.
      await expect
        .poll(
          async () => {
            const res = await request.get(`${BACKEND_URL}/subscriptions`, {
              headers: { Authorization: `Bearer ${accessToken}` },
            })
            const items = (await res.json()) as Array<{
              id: number
              is_active: boolean
            }>
            const me = items.find((s) => s.id === subscription.id)
            return me?.is_active === false
          },
          { timeout: 15_000, intervals: [200, 500, 1_000] },
        )
        .toBe(true)

      await page.reload()
      await expect(page.getByRole('heading', { name: /我的订阅/ })).toBeVisible({
        timeout: 10_000,
      })

      const pausedRow = page
        .locator('tr')
        .filter({
          has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${report.name}$`) }),
        })
      await expect(pausedRow.getByText(/^已暂停$/, { exact: true })).toBeVisible({
        timeout: 10_000,
      })

      // Resume via API.
      await request.post(`${BACKEND_URL}/subscriptions/${subscription.id}/resume`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })

      await expect
        .poll(
          async () => {
            const res = await request.get(`${BACKEND_URL}/subscriptions`, {
              headers: { Authorization: `Bearer ${accessToken}` },
            })
            const items = (await res.json()) as Array<{
              id: number
              is_active: boolean
            }>
            const me = items.find((s) => s.id === subscription.id)
            return me?.is_active === true
          },
          { timeout: 15_000, intervals: [200, 500, 1_000] },
        )
        .toBe(true)

      await page.reload()
      await expect(page.getByRole('heading', { name: /我的订阅/ })).toBeVisible({
        timeout: 10_000,
      })

      const resumedRow = page
        .locator('tr')
        .filter({
          has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${report.name}$`) }),
        })
      await expect(resumedRow.getByText(/^运行中$/, { exact: true })).toBeVisible({
        timeout: 10_000,
      })
    } finally {
      // Idempotent cleanup — covers the assertion-failed path.
      await request
        .delete(`${BACKEND_URL}/subscriptions/${subscription.id}`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        })
        .catch(() => {})
      await deleteReport(request, accessToken, report.id)
      await deleteDataSource(request, accessToken, ds.id)
    }
  })
})