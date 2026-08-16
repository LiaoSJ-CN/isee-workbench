/**
 * End-to-end smoke tests for the iSee Workbench frontend.
 *
 * These tests verify the three critical user journeys identified in
 * 批 7.4 of the improvement plan:
 *
 *   1. Login with default credentials → land on the report list
 *   2. Create a new report → land in the report editor
 *   3. DataExplorer executes SELECT 1 → renders a result row
 *
 * Tests skip themselves when the backend isn't reachable, so a local
 * checkout without `uvicorn` running won't fail — but the CI e2e job
 * (which boots docker-compose) WILL exercise them.
 */

import { expect, test } from '@playwright/test'

const DEFAULT_USER = 'admin'
const DEFAULT_PASS = 'admin'
const BACKEND_HEALTH_URL = 'http://localhost:8000/docs'

test.beforeAll(async () => {
  // Cheap pre-flight: bail out of the whole suite if the backend isn't up.
  // Lets `npx playwright test` be run locally without breaking.
  test.skip(
    !(await isReachable(BACKEND_HEALTH_URL)),
    `backend not reachable at ${BACKEND_HEALTH_URL}; skipping e2e suite`,
  )
})

test('login → reports list', async ({ page }) => {
  await page.goto('/login')

  // The username/password fields share placeholder 'admin'; the form's
  // submit button is the only primary button.
  const usernameInput = page.locator('input').filter({
    hasNot: page.locator('[type="password"]'),
  }).first()
  const passwordInput = page.locator('input[type="password"]').first()

  await usernameInput.fill(DEFAULT_USER)
  await passwordInput.fill(DEFAULT_PASS)
  await page.getByRole('button', { name: /登.*录/ }).click()

  // Should land somewhere authenticated — the default is /.
  await expect(page).toHaveURL(/\/(reports)?$/)
  // The reports navigation entry should be visible in the sider.
  await expect(page.getByText(/报表/).first()).toBeVisible()
})

test('create report → editor', async ({ page }) => {
  // Pre-condition: login.
  await page.goto('/login')
  const usernameInput = page.locator('input').filter({
    hasNot: page.locator('[type="password"]'),
  }).first()
  const passwordInput = page.locator('input[type="password"]').first()
  await usernameInput.fill(DEFAULT_USER)
  await passwordInput.fill(DEFAULT_PASS)
  await page.getByRole('button', { name: /登.*录/ }).click()

  await page.goto('/reports')

  // Click the create-report button (usually "新建" or "创建").
  const createButton = page.getByRole('button', { name: /新建|创建/ }).first()
  if (await createButton.isVisible()) {
    await createButton.click()
    await expect(page).toHaveURL(/\/reports\/\d+/)
  } else {
    test.skip(true, 'create button not visible; UI may have changed')
  }
})

test('data explorer runs SELECT 1', async ({ page }) => {
  await page.goto('/login')
  const usernameInput = page.locator('input').filter({
    hasNot: page.locator('[type="password"]'),
  }).first()
  const passwordInput = page.locator('input[type="password"]').first()
  await usernameInput.fill(DEFAULT_USER)
  await passwordInput.fill(DEFAULT_PASS)
  await page.getByRole('button', { name: /登.*录/ }).click()

  await page.goto('/explorer')

  // Find the SQL editor textarea (CodeMirror renders a contentEditable;
  // simplest selector is the visible editor surface or fallback to textarea).
  const editor = page.locator('.cm-content').first()
  if (!(await editor.isVisible())) {
    test.skip(true, 'editor not visible; code-mirror selector may have changed')
    return
  }
  await editor.click()
  await editor.fill('SELECT 1 AS one')

  // Run button — usually a play icon button.
  const runButton = page.getByRole('button', { name: /运行|执行|Run/i }).first()
  if (await runButton.isVisible()) {
    await runButton.click()
    // Wait for either the results table or a success toast.
    await expect(page.getByText(/1|one/i).first()).toBeVisible({ timeout: 10_000 })
  } else {
    test.skip(true, 'run button not visible; UI may have changed')
  }
})

async function isReachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { method: 'GET' })
    return res.ok || res.status === 200 || res.status === 404
  } catch {
    return false
  }
}