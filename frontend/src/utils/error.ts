/** Extract a user-readable error message, preferring the server's `detail`
 * field when the error came from an axios request. Falls back to the
 * caller-provided string when nothing better is available.
 */
export function formatError(err: unknown, fallback: string): string {
  if (typeof err === 'object' && err !== null) {
    const detail = (err as { response?: { data?: { detail?: unknown } } })
      .response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return fallback;
}
