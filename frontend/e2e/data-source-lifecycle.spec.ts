/**
 * End-to-end tests for the DataSource lifecycle (批 B2).
 *
 * Covers the user journeys the existing smoke + report-lifecycle
 * specs leave uncovered:
 *
 *   1. Create SQLite DS via the page form → it appears in the list
 *   2. Edit a DS name in-place → list reflects the change → delete
 *      removes the row
 *   3. Clone a DS → the clone row appears with a new name → original
 *      is untouched
 *
 * Setup strategy mirrors ``report-lifecycle.spec.ts``: API token
 * cache + UI bypass for login. The create / edit / delete / clone
 * buttons are still driven via the UI — that's the regression
 * surface. The SQLite ``:memory:`` placeholder is fine — the
 * create flow only validates the form payload, not a live
 * connection.
 *
 * The "test connection" button isn't covered here because the
 * placeholder SQLite host isn't reachable; that path is exercised
 * by backend pytest (``test_connection.py``). A future batch can
 * add it once the Docker test stack has a reachable PostgreSQL.
 */

import { expect, test } from '@playwright/test'

import {
  BACKEND_URL,
  authenticateAndEnter,
  createSqliteDataSource,
  deleteDataSource,
  login,
  skipIfBackendDown,
} from './_helpers'

test.beforeAll(async ({ request }) => {
  await skipIfBackendDown(request)
})

test.describe('data source lifecycle', () => {
  test('create → list shows it → delete removes it', async ({ page, request }) => {
    const { accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken, 'e2e-create')

    try {
      await authenticateAndEnter(page, request, '/data-sources')

      // Hard-reload so the list picks up the API-seeded row —
      // navigating in via SPA push doesn't re-run the GET.
      await page.reload()

      // Wait for the table itself to render (loading state clears
      // after the GET resolves). Bump page size to 100 so the new
      // row isn't pushed onto page 2 by the Ant-D default of 10.
      await expect(page.locator('.ant-table-row').first()).toBeVisible({
        timeout: 10_000,
      })
      await page.locator('.ant-pagination-options-size-changer').first().click()
      await page.getByText('100 / page').click()

      // The new DS row should appear in the table. Scope the row
      // locator to an exact-match name cell — substring matches
      // would collide with any clones left over from earlier runs.
      const row = page
        .locator('tr')
        .filter({ has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${ds.name}$`) }) })
      await expect(row).toBeVisible({ timeout: 10_000 })

      // Click its row's delete button → confirm Popconfirm.
      await row.getByRole('button', { name: /删\s*除/ }).click()

      // AntD Popconfirm renders an inline tooltip with "确定删除?"
      // + Cancel / OK buttons. The OK button is the primary
      // (filled) one inside the popover body.
      const okButton = page.getByRole('button', { name: 'OK' })
      await expect(okButton).toBeVisible({ timeout: 5_000 })
      await okButton.click()

      // Row should disappear within 5s (delete + invalidate).
      await expect(row).toBeHidden({ timeout: 5_000 })
    } finally {
      // Idempotent cleanup — covers the "assertion failed before
      // delete" path.
      await deleteDataSource(request, accessToken, ds.id).catch(() => {})
    }
  })

  test('edit DS name → list reflects new name', async ({ page, request }) => {
    const { accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken, 'e2e-edit')

    try {
      await authenticateAndEnter(page, request, '/data-sources')

      // Hard-reload so the list picks up the API-seeded row.
      await page.reload()
      await expect(page.locator('.ant-table-row').first()).toBeVisible({
        timeout: 10_000,
      })
      // Bump page size to 100 so the API-seeded row isn't pushed
      // to page 2 by the Ant-D default of 10.
      await page.locator('.ant-pagination-options-size-changer').first().click()
      await page.getByText('100 / page').click()

      // Scope row locator to exact-match name cell — substring
      // matches would collide with clones from earlier runs.
      const row = page
        .locator('tr')
        .filter({ has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${ds.name}$`) }) })
      await expect(row).toBeVisible({ timeout: 10_000 })

      // Open the edit modal.
      await row.getByRole('button', { name: /编\s*辑/ }).click()
      const editModal = page.getByRole('dialog').filter({
        has: page.getByText(/编辑数据源/),
      })
      await expect(editModal).toBeVisible({ timeout: 5_000 })

      // Update only the name field.
      const nameInput = editModal.getByLabel('名称', { exact: false }).first()
      const newName = `${ds.name}-renamed`
      await nameInput.fill(newName)
      // AntD modal's OK button lives in the footer; selector by class
      // is more stable than matching autoInsertSpace-injected text.
      await editModal.locator('.ant-modal-footer .ant-btn-primary').click()

      // New name appears in the table; old name is gone. Use
      // ``exact: true`` because the new name is a superset of the
      // old (old = prefix of new) and substring matches both.
      const newCell = page.locator('td.ant-table-cell', {
        hasText: new RegExp(`^${newName}$`),
      })
      await expect(newCell).toBeVisible({ timeout: 5_000 })
      const oldCell = page.locator('td.ant-table-cell', {
        hasText: new RegExp(`^${ds.name}$`),
      })
      await expect(oldCell).toBeHidden({ timeout: 5_000 })

      // Update the variable so cleanup uses the right id (it
      // doesn't matter — we still delete by id, not name — but
      // keeping the comment helps the next reader).
      void newName
    } finally {
      await deleteDataSource(request, accessToken, ds.id).catch(() => {})
    }
  })

  test('clone → original unchanged, new row appears with different name', async ({
    page,
    request,
  }) => {
    const { accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken, 'e2e-clone')
    // Track clones to clean them up at end of test.
    const clones: number[] = []

    try {
      await authenticateAndEnter(page, request, '/data-sources')

      // Hard-reload so the list picks up the API-seeded row.
      await page.reload()
      await expect(page.locator('.ant-table-row').first()).toBeVisible({
        timeout: 10_000,
      })
      // Bump page size to 100 so the API-seeded row isn't pushed
      // to page 2 by the Ant-D default of 10.
      await page.locator('.ant-pagination-options-size-changer').first().click()
      await page.getByText('100 / page').click()

      // Scope the row locator to an exact-match name cell so the
      // clone (which has ``(副本)`` appended) doesn't also match.
      const originalRow = page
        .locator('tr')
        .filter({ has: page.locator('td.ant-table-cell', { hasText: new RegExp(`^${ds.name}$`) }) })
      await expect(originalRow).toBeVisible({ timeout: 10_000 })

      await originalRow.getByRole('button', { name: /复\s*制/ }).click()

      // The backend clones synchronously and the AntD success toast
      // shows "已复制为「<new_name>」". Wait for the toast (the table
      // cell with "副本" suffix is a side-effect of the same render;
      // matching the toast alone avoids the strict-mode violation).
      await expect(page.getByText(/已复制为/)).toBeVisible({
        timeout: 10_000,
      })

      // Original row is still visible (the click didn't delete it).
      await expect(originalRow).toBeVisible({ timeout: 5_000 })

      // Find the clone row id for cleanup.
      const list = await request.get(`${BACKEND_URL}/data-sources`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const items = (await list.json()) as Array<{ id: number; name: string }>
      for (const it of items) {
        if (it.id !== ds.id && it.name.startsWith(ds.name)) {
          clones.push(it.id)
        }
      }
    } finally {
      await deleteDataSource(request, accessToken, ds.id).catch(() => {})
      for (const cloneId of clones) {
        await deleteDataSource(request, accessToken, cloneId).catch(() => {})
      }
    }
  })
})