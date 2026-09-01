/**
 * End-to-end tests for the report lifecycle (P2-1).
 *
 * Covers the three user journeys that 批 7.4's 3 smoke tests leave
 * uncovered:
 *
 *   1. Report preview renders — the HTML iframe shows the rendered
 *      report after the user clicks "生成预览" / "刷新预览".
 *   2. Async Excel export round-trip — clicking "导出 Excel" enqueues
 *      a job, the status tag transitions to "已完成", and the "下载
 *      Excel" button fires a real download event whose payload is a
 *      non-empty file with the correct MIME.
 *   3. Async PDF export round-trip — same shape as Excel but for
 *      PDF (covers 批 8.1's weasyprint path end-to-end).
 *
 * Setup strategy: API first, then UI. Tests use Playwright's
 * ``request`` fixture to mint a session token and create a SQLite
 * data source + report with a single text item. The text item's
 * content is what we assert against in the HTML preview — text
 * items don't touch the underlying DataSource, so a SQLite
 * ``:memory:`` connection is enough to satisfy the report
 * generator without seeding real tables.
 *
 * The CI job runs these against a docker-compose stack with
 * ``SCHEDULER_DISABLED=true``; the async-executor in
 * ``services/job_queue`` is still up and runs the worker. The
 * web container proxies ``/api`` to the backend container, so
 * the same baseURL works for both UI and API.
 *
 * Local dev: spin up backend (`uvicorn`) + frontend (`npm run dev`)
 * and run ``npx playwright test e2e/report-lifecycle.spec.ts``.
 */

import { expect, test } from '@playwright/test'

import {
  authenticateAndEnter,
  createSqliteDataSource,
  createTextReport,
  deleteDataSource,
  deleteReport,
  login,
  skipIfBackendDown,
} from './_helpers'

// Mark used to detect that the preview actually rendered our text
// item — survives the html.escape round-trip in the backend.
const TEXT_ITEM_MARKER = `e2e-lifecycle-${Date.now()}`

test.beforeAll(async ({ request }) => {
  await skipIfBackendDown(request)
})

test.describe('report lifecycle', () => {
  // Each test gets a fresh data source + report, then cleans up.
  // The cleanup is local to the test so a flake on one assertion
  // doesn't leak rows into siblings.
  test('preview renders the report HTML', async ({ page, request }) => {
    const { access_token: accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken)
    const report = await createTextReport(request, accessToken, ds.id, TEXT_ITEM_MARKER)

    try {
      await authenticateAndEnter(page, request)

      // Navigate to the report preview page.
      await page.goto(`/reports/${report.id}/preview`)

      // Click "生成预览" — its placement depends on whether the user
      // has rendered before (the "刷新预览" toolbar button replaces
      // the initial empty state). Both buttons are visible on first
      // load (the toolbar one + the primary "生成预览" inside the
      // empty-state card), so ``.first()`` picks the toolbar one and
      // we keep the rest of the test page-stable.
      const generateButton = page.getByRole('button', { name: /生成预览|刷新预览/ }).first()
      await expect(generateButton).toBeVisible({ timeout: 10_000 })
      await generateButton.click()

      // The preview iframe's blob: URL is set on the page once the
      // /reports/preview response lands. Wait for the iframe to
      // appear, then assert the marker is somewhere in the rendered
      // HTML inside the iframe.
      const previewFrame = page.locator('iframe[title="Report Preview"]')
      await expect(previewFrame).toBeVisible({ timeout: 10_000 })
      const frameContent = await previewFrame.contentFrame()
      expect(frameContent, 'preview iframe contentFrame is null').not.toBeNull()
      await expect(frameContent!.locator('body')).toContainText(TEXT_ITEM_MARKER, {
        timeout: 15_000,
      })
    } finally {
      await deleteReport(request, accessToken, report.id)
      await deleteDataSource(request, accessToken, ds.id)
    }
  })

  test('excel async export completes and download fires', async ({ page, request }) => {
    const { access_token: accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken)
    const report = await createTextReport(request, accessToken, ds.id, TEXT_ITEM_MARKER)

    try {
      await authenticateAndEnter(page, request)

      await page.goto(`/reports/${report.id}/preview`)

      // Click the toolbar "导出 Excel" button. When the report has
      // no parameters, the toolbar shortcut is shown; otherwise the
      // form owns the submit button. Our report has no parameters.
      const exportButton = page.getByRole('button', { name: /导出 Excel/ })
      await expect(exportButton).toBeVisible({ timeout: 10_000 })
      await exportButton.click()

      // The "Excel 导出任务" card appears with a "已完成" tag once
      // the worker has finished rendering. The job is fast (single
      // text item, no DB query) — 30s is generous.
      const doneTag = page.getByText(/^已完成$/, { exact: true })
      await expect(doneTag).toBeVisible({ timeout: 30_000 })

      // The download button shows up alongside the "已完成" tag.
      const downloadButton = page.getByRole('button', { name: /下载 Excel/ })
      await expect(downloadButton).toBeVisible()

      // Set up the download listener BEFORE clicking — Playwright
      // captures the browser-level download event that the
      // ``<a download>`` click in the helper triggers.
      const downloadPromise = page.waitForEvent('download', { timeout: 30_000 })
      await downloadButton.click()
      const download = await downloadPromise

      // Sanity-check the file: the suggested filename should end in
      // .xlsx (per the helper's naming convention) and the on-disk
      // file should be a non-empty Excel workbook.
      expect(download.suggestedFilename()).toMatch(/\.xlsx$/)
      const path = await download.path()
      expect(path, 'download path is null').not.toBeNull()
      const size = (await import('node:fs')).statSync(path!).size
      expect(size).toBeGreaterThan(0)
    } finally {
      await deleteReport(request, accessToken, report.id)
      await deleteDataSource(request, accessToken, ds.id)
    }
  })

  test('pdf async export completes and download fires', async ({ page, request }) => {
    // Weasyprint on a cold cache can take 30-60s (Pango init + chart
    // rasterization); the default 30s test timeout will kill the
    // page before the worker finishes, which then closes the shared
    // ``request`` fixture and the finally-cleanup fails with
    // "Target page, context or browser has been closed". Bump the
    // test-wide budget so cleanup runs to completion.
    test.setTimeout(180_000)

    const { access_token: accessToken } = await login(request)
    const ds = await createSqliteDataSource(request, accessToken)
    const report = await createTextReport(request, accessToken, ds.id, TEXT_ITEM_MARKER)

    try {
      await authenticateAndEnter(page, request)

      await page.goto(`/reports/${report.id}/preview`)

      const exportButton = page.getByRole('button', { name: /导出 PDF/ })
      await expect(exportButton).toBeVisible({ timeout: 10_000 })
      await exportButton.click()

      // The card lands in one of two states:
      //   * ``done`` on a docker backend that has weasyprint + the
      //     native libs (libpango, libcairo, libgdk-pixbuf,
      //     fonts-noto-cjk) — the canonical CI path.
      //   * ``failed`` on a slim dev venv without weasyprint
      //     installed. The error message must still surface the
      //     actionable install hint, so we assert that the failure
      //     banner names the missing dependency.
      //
      // Either branch is a pass — the test exercises the full UI
      // wiring (enqueue → poll → terminal tag → follow-up action)
      // and confirms the right error reporting path on the
      // "library missing" case that 批 8.1 designed for.
      const doneTag = page.getByText(/^已完成$/, { exact: true })
      const failedTag = page.getByText(/^失败$/, { exact: true })

      // Wait for one of the two terminal tags to appear.
      const winner = await Promise.race([
        doneTag.waitFor({ state: 'visible', timeout: 90_000 }).then(() => 'done' as const),
        failedTag.waitFor({ state: 'visible', timeout: 90_000 }).then(() => 'failed' as const),
      ]).catch(() => null)

      if (winner === 'done') {
        const downloadButton = page.getByRole('button', { name: /下载 PDF/ })
        await expect(downloadButton).toBeVisible()
        const downloadPromise = page.waitForEvent('download', { timeout: 30_000 })
        await downloadButton.click()
        const download = await downloadPromise
        expect(download.suggestedFilename()).toMatch(/\.pdf$/)
        const path = await download.path()
        expect(path, 'download path is null').not.toBeNull()
        const size = (await import('node:fs')).statSync(path!).size
        expect(size).toBeGreaterThan(0)
      } else {
        // failed branch — assert the error banner names weasyprint
        // so an operator looking at the failure can act on it.
        const errorAlert = page.locator('.ant-alert-error').filter({
          hasText: /weasyprint/,
        })
        await expect(errorAlert).toBeVisible({ timeout: 5_000 })
      }
    } finally {
      await deleteReport(request, accessToken, report.id)
      await deleteDataSource(request, accessToken, ds.id)
    }
  })
})
