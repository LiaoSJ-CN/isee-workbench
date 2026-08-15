import { QueryClient } from '@tanstack/react-query';

/**
 * Single QueryClient instance for the whole app.
 *
 * `retry: false` because `frontend/src/api/index.ts:41-79` already handles
 * 401 by attempting a refresh once, then hard-redirecting to /login. An
 * RQ-level retry on top of that would trigger a second refresh attempt
 * (the refresh token is single-use) and a double redirect.
 *
 * `staleTime: 30_000` — within a 30 s window, navigating between pages
 * hits the cache without a refetch; crossing the window triggers a
 * background refetch.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: false,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
    },
    mutations: {
      retry: false,
    },
  },
});
