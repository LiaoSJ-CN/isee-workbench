import type { UseQueryResult } from '@tanstack/react-query';
import { useMe } from './useAuth';
import type { CurrentUser, Report } from '../types';

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

/**
 * Owner-or-admin check for a single Report — matches
 * ``is_owner_or_admin`` in ``backend/app/services/report.py``.
 *
 * Admins always pass; otherwise the report must have an
 * ``owner_user_id`` equal to the current user's ``user_id``.
 * Pass the full ``Report`` (or any object exposing
 * ``owner_user_id``) so the call site can rely on the server-rendered
 * shape after a list/detail fetch.
 */
export function isOwnerOrAdmin(
  userOrResult: CurrentUser | UseQueryResult<CurrentUser> | null | undefined,
  report: Pick<Report, 'owner_user_id'> | null | undefined,
): boolean {
  if (!report) return false;
  if (isAdmin(userOrResult)) return true;
  const user: CurrentUser | null | undefined =
    userOrResult && 'data' in userOrResult
      ? userOrResult.data
      : (userOrResult as CurrentUser | null | undefined);
  return !!user && report.owner_user_id === user.user_id;
}
