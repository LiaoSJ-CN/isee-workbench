import type { UseQueryResult } from '@tanstack/react-query';
import { useMe } from './useAuth';
import type { CurrentUser } from '../types';

/**
 * Thin wrapper over `useMe` exposing a friendlier name.
 *
 * T13 (Report Versioning) and downstream T14+ tasks import
 * `useCurrentUser` / `isAdmin` for permission gating — keeping the
 * alias here lets the page code stay role-centric while `useAuth`
 * remains the single source of truth for session queries.
 */
export function useCurrentUser(): UseQueryResult<CurrentUser> {
  return useMe();
}

/**
 * Server enforces owner-or-admin on every mutating version endpoint.
 * The UI mirrors that gate so non-admins don't see a button that
 * would 403 on click.
 *
 * Accepts both the raw `CurrentUser` (after a manual `.data` unwrap)
 * and the full `UseQueryResult` so call sites stay terse —
 * `isAdmin(useCurrentUser())` works either way.
 */
export function isAdmin(
  userOrResult: CurrentUser | UseQueryResult<CurrentUser> | null | undefined,
): boolean {
  if (!userOrResult) return false;
  // `UseQueryResult` carries `.data` (and `.role` is not on it), so
  // a property-access check distinguishes the two shapes.
  if ('data' in userOrResult) {
    return userOrResult.data?.role === 'admin';
  }
  return userOrResult.role === 'admin';
}
