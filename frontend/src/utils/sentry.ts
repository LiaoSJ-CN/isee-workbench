/**
 * Sentry initialization for the frontend (批 6a).
 *
 * Mirrors the backend pattern: when ``VITE_SENTRY_DSN`` is empty (the
 * default for local dev), nothing is imported past the feature check —
 * so the bundle pays no cost and no network traffic is generated.
 *
 * When the env var is set we:
 * - Initialize the SDK once at app startup with the React Router /
 *   browser-tracing integrations.
 * - Surface a ``captureException`` helper so route components can
 *   report caught errors without importing the SDK directly.
 *
 * The backend already tags events with the request id via the
 * ``X-Request-ID`` response header, but that correlation is server-side.
 * Frontend-initiated errors (uncaught promise rejections, React render
 * errors) carry no such id — Sentry links them via the user's session
 * instead. Both halves stay independent.
 */

import * as Sentry from '@sentry/react';

let initialized = false;

/**
 * Initialize Sentry if ``VITE_SENTRY_DSN`` is set. Idempotent —
 * subsequent calls are no-ops so it's safe to call from ``main.tsx``
 * and from individual route tests.
 */
export function initSentry(): boolean {
  if (initialized) return true;
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return false;

  Sentry.init({
    dsn,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || undefined,
    // Trace sample rate defaults to 0.1 in production. Set via env if
    // you want a different rate — 0 disables performance entirely.
    tracesSampleRate:
      Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE) || 0.1,
  });
  initialized = true;
  return true;
}

/**
 * Forward an error to Sentry. No-op when Sentry isn't initialized,
 * so callers don't need to gate calls.
 */
export function captureException(
  err: unknown,
  context?: Record<string, unknown>,
): void {
  if (!initialized) return;
  if (context) {
    Sentry.captureException(err, { extra: context });
  } else {
    Sentry.captureException(err);
  }
}

/** Re-export the SDK's React error boundary for routes that want it. */
export { Sentry };