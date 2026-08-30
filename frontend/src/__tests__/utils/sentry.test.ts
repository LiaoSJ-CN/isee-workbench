/** Tests for the frontend Sentry initialization (批 F — Sentry PROD 接入).
 *
 * Mirrors the backend ``test_sentry_init.py`` coverage but adapted for
 * the Vite/vitest environment:
 * - ``import.meta.env`` values are stubbed with ``vi.stubEnv`` per test.
 * - ``@sentry/react`` is mocked at the module boundary so we can
 *   observe what gets passed to ``Sentry.init`` / ``Sentry.captureException``.
 * - The module's internal ``initialized`` flag is module-scoped, so each
 *   "DSN set" test resets modules before importing to start from a clean
 *   slate. The idempotency describe block keeps a single import and
 *   exercises ``initSentry`` twice to confirm the flag short-circuits.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockSentryInit = vi.fn();
const mockCaptureException = vi.fn();

vi.mock('@sentry/react', () => ({
  init: (...args: unknown[]) => mockSentryInit(...args),
  captureException: (...args: unknown[]) => mockCaptureException(...args),
}));

async function loadSentryModule() {
  // Each load yields a fresh ``initialized = false`` flag — that's how we
  // get per-test isolation while still allowing the dedicated describe
  // block below to exercise idempotency within a single instance.
  vi.resetModules();
  return import('../../utils/sentry');
}

afterEach(() => {
  vi.unstubAllEnvs();
  mockSentryInit.mockReset();
  mockCaptureException.mockReset();
});

describe('initSentry — feature flag', () => {
  it('returns false and skips SDK init when VITE_SENTRY_DSN is empty', async () => {
    const sentry = await loadSentryModule();
    expect(sentry.initSentry()).toBe(false);
    expect(mockSentryInit).not.toHaveBeenCalled();
  });

  it('returns false when VITE_SENTRY_DSN is unset entirely', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', '');
    const sentry = await loadSentryModule();
    expect(sentry.initSentry()).toBe(false);
    expect(mockSentryInit).not.toHaveBeenCalled();
  });
});

describe('initSentry — SDK configuration', () => {
  it('initializes the SDK with the configured DSN', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    const sentry = await loadSentryModule();

    expect(sentry.initSentry()).toBe(true);
    expect(mockSentryInit).toHaveBeenCalledTimes(1);
    expect(mockSentryInit).toHaveBeenCalledWith(
      expect.objectContaining({ dsn: 'https://key@sentry.io/123' }),
    );
  });

  it('uses 0.1 as the default tracesSampleRate when no env var is set', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    // Ensure no tracesSampleRate override leaks from another test.
    vi.stubEnv('VITE_SENTRY_TRACES_SAMPLE_RATE', '');
    const sentry = await loadSentryModule();

    sentry.initSentry();
    expect(mockSentryInit).toHaveBeenCalledWith(
      expect.objectContaining({ tracesSampleRate: 0.1 }),
    );
  });

  it('honours VITE_SENTRY_TRACES_SAMPLE_RATE when set', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    vi.stubEnv('VITE_SENTRY_TRACES_SAMPLE_RATE', '0.05');
    const sentry = await loadSentryModule();

    sentry.initSentry();
    expect(mockSentryInit).toHaveBeenCalledWith(
      expect.objectContaining({ tracesSampleRate: 0.05 }),
    );
  });

  it('falls back to 0.1 when VITE_SENTRY_TRACES_SAMPLE_RATE is not a number', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    vi.stubEnv('VITE_SENTRY_TRACES_SAMPLE_RATE', 'not-a-number');
    const sentry = await loadSentryModule();

    sentry.initSentry();
    // Number('not-a-number') === NaN → falsy → default 0.1 applies.
    expect(mockSentryInit).toHaveBeenCalledWith(
      expect.objectContaining({ tracesSampleRate: 0.1 }),
    );
  });

  it('passes through VITE_SENTRY_ENVIRONMENT when set', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    vi.stubEnv('VITE_SENTRY_ENVIRONMENT', 'production');
    const sentry = await loadSentryModule();

    sentry.initSentry();
    expect(mockSentryInit).toHaveBeenCalledWith(
      expect.objectContaining({ environment: 'production' }),
    );
  });

  it('passes ``undefined`` environment when the env var is unset', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    // No stubEnv for VITE_SENTRY_ENVIRONMENT — the source code uses
    // ``|| undefined`` so an empty string also collapses to undefined.
    const sentry = await loadSentryModule();

    sentry.initSentry();
    expect(mockSentryInit).toHaveBeenCalledWith(
      expect.objectContaining({ environment: undefined }),
    );
  });
});

describe('initSentry — idempotency', () => {
  // No ``vi.resetModules`` between calls — this exercises the same module
  // instance twice so the internal ``initialized`` flag short-circuits
  // the second ``init`` call.
  beforeEach(() => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
  });

  it('only calls Sentry.init once across repeated initSentry() invocations', async () => {
    const sentry = await loadSentryModule();
    expect(sentry.initSentry()).toBe(true);
    // Second call returns true (already initialised) but does not re-init.
    expect(sentry.initSentry()).toBe(true);
    expect(mockSentryInit).toHaveBeenCalledTimes(1);
  });
});

describe('captureException', () => {
  it('is a no-op when Sentry has not been initialized', async () => {
    const sentry = await loadSentryModule();
    // No DSN → initSentry returns false → captureException must do nothing.
    sentry.captureException(new Error('boom'));
    expect(mockCaptureException).not.toHaveBeenCalled();
  });

  it('forwards an error to Sentry when initialized (no context)', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    const sentry = await loadSentryModule();
    sentry.initSentry();

    const err = new Error('network down');
    sentry.captureException(err);

    expect(mockCaptureException).toHaveBeenCalledTimes(1);
    expect(mockCaptureException).toHaveBeenCalledWith(err);
  });

  it('wraps context under the ``extra`` key when provided', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    const sentry = await loadSentryModule();
    sentry.initSentry();

    const err = new Error('query failed');
    sentry.captureException(err, { sql: 'SELECT 1', rowCount: 0 });

    expect(mockCaptureException).toHaveBeenCalledWith(err, {
      extra: { sql: 'SELECT 1', rowCount: 0 },
    });
  });

  it('does not forward non-Error values to Sentry in a special way', async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://key@sentry.io/123');
    const sentry = await loadSentryModule();
    sentry.initSentry();

    // Strings, plain objects, undefined should pass through unchanged —
    // Sentry's SDK normalises them.
    sentry.captureException('just a string');
    sentry.captureException({ detail: 'oops' });
    sentry.captureException(undefined);

    expect(mockCaptureException).toHaveBeenCalledTimes(3);
    expect(mockCaptureException).toHaveBeenNthCalledWith(1, 'just a string');
    expect(mockCaptureException).toHaveBeenNthCalledWith(2, { detail: 'oops' });
    expect(mockCaptureException).toHaveBeenNthCalledWith(3, undefined);
  });
});
