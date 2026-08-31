/**
 * ``useAdminUsers`` + ``useAdminGrants`` — admin-only query layer for the
 * user-management batch (批 user-management S3+S4).
 *
 * Surface mirrors backend ``app/routers/admin_users.py`` and
 * ``app/routers/admin_grants.py``. The frontend gates routes with
 * ``<RequireAdmin>`` (App.tsx:164) so non-admins never reach these
 * hooks; the backend double-checks via ``admin_required``.
 *
 * Invalidation convention:
 *  - User mutation → ``adminUsers.all`` (covers list + detail + grants).
 *  - Grant mutation → ``adminUsers.all`` (covers drawer tabs) +
 *    ``dataSources.acl(rid)`` + ``reports.shares(rid)`` +
 *    ``['dashboard', rid, 'shares']`` — per-resource share modals stay
 *    fresh after a centralised grant/revoke. The dashboard prefix
 *    matches ``DashboardEdit.tsx:77``'s inline key (no factory yet —
 *    kept raw here to avoid an unrelated refactor).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { adminGrantsApi, adminUsersApi } from '../api';
import type {
  AdminGrantCreate,
  AdminResourceType,
  AdminUserRole,
  GrantSummaryItem,
  PasswordResetRequest,
  UserCreate,
  UserListResponse,
  UserResponse,
  UserUpdate,
} from '../types';
import { queryKeys } from './keys';

// ---- queries --------------------------------------------------------------

export interface AdminUserListFilters {
  role?: AdminUserRole;
  disabled?: boolean;
  q?: string;
  limit?: number;
  offset?: number;
}

/** Paginated user list. ``enabled: true`` so callers mount with eager
 *  fetch — the Users page always wants the full fleet on entry. */
export function useAdminUsers(filters?: AdminUserListFilters) {
  return useQuery<UserListResponse>({
    queryKey: queryKeys.adminUsers.list(filters),
    queryFn: () => adminUsersApi.list(filters),
  });
}

/** Single user detail. Disabled when ``id`` is null so the drawer
 *  doesn't fire before the row is picked. */
export function useAdminUser(id: number | null | undefined) {
  return useQuery<UserResponse>({
    queryKey: id != null ? queryKeys.adminUsers.detail(id) : queryKeys.adminUsers.detail(-1),
    queryFn: () => adminUsersApi.get(id as number),
    enabled: id != null,
    retry: false,
  });
}

/** Aggregated ACL view for one user — drives the UserDetailDrawer's
 *  three resource-tabs. Disabled when ``id`` is null. */
export function useAdminUserGrants(id: number | null | undefined) {
  return useQuery({
    queryKey: id != null ? queryKeys.adminUsers.grants(id) : queryKeys.adminUsers.grants(-1),
    queryFn: () => adminUsersApi.grants(id as number),
    enabled: id != null,
    retry: false,
  });
}

/** Grants pointing at one resource — drives the GrantModal preview
 *  panel. Disabled until the user has picked a (resource_type,
 *  resource_id) pair (both required). */
export function useAdminResourceGrants(
  resourceType: AdminResourceType | null | undefined,
  resourceId: number | null | undefined,
) {
  return useQuery<GrantSummaryItem[]>({
    queryKey:
      resourceType && resourceId != null
        ? queryKeys.adminGrants.byResource(resourceType, resourceId)
        : queryKeys.adminGrants.byResource('__none__', -1),
    queryFn: () => adminGrantsApi.byResource(resourceType as AdminResourceType, resourceId as number),
    enabled: resourceType != null && resourceId != null,
    retry: false,
  });
}

// ---- mutations ------------------------------------------------------------

/** Create a user. 409 on duplicate username bubbles up to the form's
 *  onError handler (form displays the message inline). */
export function useAdminCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: UserCreate) => adminUsersApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.adminUsers.all });
    },
  });
}

/** Patch role / disabled. Self-protection (last-admin demote / self-
 *  disable) raises 403 server-side; surface verbatim. Invalidates
 *  the detail and the list so the drawer and the table both refresh. */
export function useAdminUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UserUpdate }) =>
      adminUsersApi.update(id, payload),
    onSuccess: (_user, { id }) => {
      void qc.invalidateQueries({ queryKey: queryKeys.adminUsers.detail(id) });
      void qc.invalidateQueries({ queryKey: queryKeys.adminUsers.all });
    },
  });
}

/** Soft-disable (DELETE overloaded). Same self-protection rule; on
 *  success the row's ``disabled`` flag flips and the table re-renders. */
export function useAdminDisableUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => adminUsersApi.disable(id),
    onSuccess: (user) => {
      void qc.invalidateQueries({ queryKey: queryKeys.adminUsers.detail(user.id) });
      void qc.invalidateQueries({ queryKey: queryKeys.adminUsers.all });
    },
  });
}

/** Two-mode password reset. Returns the response (potentially
 *  carrying the generated plaintext) — the ResetPasswordModal owns
 *  the "show once then lose" UI. No cache invalidation — the user row
 *  is unchanged. */
export function useAdminResetUserPassword() {
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: PasswordResetRequest }) =>
      adminUsersApi.resetPassword(id, payload),
  });
}

/**
 * Cross-view centralised grant — this is the hook that proves the
 * cache invalidation invariant from the S2 plan: a grant issued from
 * the admin page must show up in the per-resource share modal the
 * next time the operator opens it.
 *
 * Invalidation reaches four sub-trees:
 *  - adminUsers.all: the drawer tab (UserDetailDrawer reads from
 *    ``useAdminUserGrants``) refetches.
 *  - adminGrants.all: the GrantModal preview panel refetches.
 *  - dataSources.acl / reports.shares: the per-resource share modal
 *    refetches so the operator sees the new row without a hard reload.
 *  - ['dashboard', resource_id, 'shares']: the DashboardEdit share
 *    panel refetches. Inline key path because there's no factory yet
 *    (matches ``DashboardEdit.tsx:77``).
 */
export function useAdminGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AdminGrantCreate) => adminGrantsApi.create(payload),
    onSuccess: (_grant, payload) => {
      void qc.invalidateQueries({ queryKey: queryKeys.adminUsers.all });
      void qc.invalidateQueries({ queryKey: queryKeys.adminGrants.all });
      if (payload.resource_type === 'data_source') {
        void qc.invalidateQueries({
          queryKey: queryKeys.dataSources.acl(payload.resource_id),
        });
      } else if (payload.resource_type === 'report') {
        void qc.invalidateQueries({
          queryKey: queryKeys.reports.shares(payload.resource_id),
        });
      } else if (payload.resource_type === 'dashboard') {
        // DashboardEdit uses the raw key `['dashboard', id, 'shares']`.
        // No factory yet — match the inline pattern.
        void qc.invalidateQueries({
          queryKey: ['dashboard', payload.resource_id, 'shares'],
        });
      }
    },
  });
}

/** Centralised revoke — same four-key invalidation pattern as
 *  ``useAdminGrant`` (mirrored on revoke so a removed grant
 *  disappears from all views). */
export function useAdminRevokeGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      resource_type,
      grant_id,
    }: {
      resource_type: AdminResourceType;
      grant_id: number;
      // Forwarded so the invalidation can target the same per-resource
      // caches without a second read.
      resource_id: number;
    }) => adminGrantsApi.revoke(resource_type, grant_id),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: queryKeys.adminUsers.all });
      void qc.invalidateQueries({ queryKey: queryKeys.adminGrants.all });
      if (vars.resource_type === 'data_source') {
        void qc.invalidateQueries({
          queryKey: queryKeys.dataSources.acl(vars.resource_id),
        });
      } else if (vars.resource_type === 'report') {
        void qc.invalidateQueries({
          queryKey: queryKeys.reports.shares(vars.resource_id),
        });
      } else if (vars.resource_type === 'dashboard') {
        void qc.invalidateQueries({
          queryKey: ['dashboard', vars.resource_id, 'shares'],
        });
      }
    },
  });
}