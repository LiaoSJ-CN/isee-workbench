import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { authApi } from '../api';
import { queryKeys } from './keys';

/**
 * `useMe` — the gate for `RequireAuth`.
 *
 * Override the global defaults:
 * - `retry: false` — 401 from /auth/me is the "not logged in" signal;
 *   the axios refresh interceptor handles the redirect.
 * - `staleTime: 5 min` — session identity is stable for a session.
 * - `gcTime: Infinity` — keep the cache for the lifetime of the page;
 *   no need to evict a known-good session.
 * - `refetchOnWindowFocus: false` — refocusing the tab shouldn't re-validate
 *   a still-valid session.
 */
export function useMe(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.auth.me,
    queryFn: authApi.me,
    retry: false,
    staleTime: 5 * 60_000,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
    ...options,
  });
}

interface LoginInput {
  username: string;
  password: string;
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ username, password }: LoginInput) =>
      authApi.login(username, password),
    onSuccess: () => {
      // Force `RequireAuth` to re-evaluate and let the user in.
      void qc.invalidateQueries({ queryKey: queryKeys.auth.me });
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => authApi.logout(),
    onSuccess: () => {
      qc.clear(); // wipe all caches on logout
    },
  });
}
