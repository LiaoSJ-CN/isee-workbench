import { defineConfig } from '@playwright/test'

/**
 * Playwright config for end-to-end smoke tests.
 *
 * These tests verify the user-visible critical paths: login, navigate
 * to the report editor, run an explorer query. They require a running
 * backend (default: http://localhost:8000) and frontend (default:
 * http://localhost:5173). For local runs:
 *
 *   # In one terminal:
 *   cd backend && source .venv/bin/activate && uvicorn app.main:app --reload
 *   # In another:
 *   cd frontend && npm run dev
 *   # In a third:
 *   cd frontend && npx playwright test
 *
 * For CI: the e2e job boots docker-compose, waits for health checks,
 * then runs the suite.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
})