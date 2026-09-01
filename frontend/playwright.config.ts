import { defineConfig } from '@playwright/test'

/**
 * Playwright config for the end-to-end suite.
 *
 * Isolation model
 * ---------------
 * The suite creates real data sources / reports / schedules / users
 * through the API. Pointing it at the developer's live backend meant
 * every run left ``e2e-*`` rows behind in ``backend/app.db`` (cleanup
 * failures were swallowed silently). So a local run now boots its own
 * throwaway stack:
 *
 *   - uvicorn on :8001 with ``DATABASE_URL=sqlite:///./e2e.db``
 *     (the file is deleted before every run — Alembic rebuilds it and
 *     the lifespan seeds the bootstrap admin)
 *   - vite dev server on :5174 with ``VITE_PROXY_TARGET`` pointed at
 *     that backend, so the SPA's ``/api`` calls land there too
 *
 * The developer's own ``npm run dev`` (:5173) and ``uvicorn`` (:8000)
 * are never touched, and neither is ``app.db``.
 *
 * Setting ``E2E_BASE_URL`` opts out of the managed servers and runs
 * against whatever is already up — that's the CI path, where the e2e
 * job boots docker-compose with its own ``/tmp/test.db``. See
 * ``e2e/_helpers.ts`` for the matching ``BACKEND_URL`` default.
 *
 *   # Local, nothing else needed:
 *   cd frontend && npx playwright test
 */
const EXTERNAL_STACK = !!process.env.E2E_BASE_URL

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5174',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: EXTERNAL_STACK
    ? undefined
    : [
        {
          // ``rm -f`` before boot rather than a teardown hook: a crashed
          // run then still starts from a clean database, and the file is
          // left on disk afterwards for post-mortem inspection.
          command:
            'rm -f e2e.db && .venv/bin/python -m uvicorn app.main:app ' +
            '--host 127.0.0.1 --port 8001',
          cwd: '../backend',
          url: 'http://127.0.0.1:8001/docs',
          reuseExistingServer: false,
          timeout: 120_000,
          env: {
            DATABASE_URL: 'sqlite:///./e2e.db',
            ADMIN_USERNAME: 'admin',
            ADMIN_PASSWORD: 'admin',
            // The CSRF middleware whitelists CORS_ORIGINS; the SPA under
            // test is served from :5174, not the :5173 default.
            CORS_ORIGINS: '["http://localhost:5174","http://127.0.0.1:5174"]',
            // Fixed so tokens stay valid if the suite outlives a reload.
            JWT_SECRET_KEY: 'e2e-secret-do-not-use-in-prod',
            // The suite logs in once per test; the 10/min production
            // default would 429 halfway through.
            LOGIN_RATE_LIMIT: '1000',
            SCHEDULER_DISABLED: 'true',
            GENERATED_REPORTS_DIR: 'e2e_generated_reports',
          },
        },
        {
          command: 'npm run dev -- --port 5174 --strictPort',
          url: 'http://localhost:5174',
          reuseExistingServer: false,
          timeout: 120_000,
          env: { VITE_PROXY_TARGET: 'http://127.0.0.1:8001' },
        },
      ],
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
})
