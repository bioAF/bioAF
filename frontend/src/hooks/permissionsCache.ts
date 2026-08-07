// Leaf module for the current user's permissions cache. It imports nothing from
// the app (no api client, no auth) so that api.ts can clear the cache on auth
// changes without creating an import cycle (api.ts -> usePermissions -> api.ts
// would re-enter api.ts during module init and trip an ApiError TDZ error under
// the settings tests' jest.requireActual mock). usePermissions reads and writes
// the shared holder here. Mirrors capabilitiesCache.

interface PermissionsCacheHolder {
  permissions: Set<string> | null;
  roleName: string | null;
  promise: Promise<void> | null;
  /**
   * Whether the last attempt failed. Held separately from `permissions` on
   * purpose: an empty permission set is a legitimate answer for a locked-down
   * role, so it cannot double as the failure signal. Never populated as a
   * "result", so a failure is not cached for the life of the tab.
   */
  failed: boolean;
}

export const permissionsCache: PermissionsCacheHolder = {
  permissions: null,
  roleName: null,
  promise: null,
  failed: false,
};

export function clearPermissionsCache(): void {
  permissionsCache.permissions = null;
  permissionsCache.roleName = null;
  permissionsCache.promise = null;
  permissionsCache.failed = false;
}
