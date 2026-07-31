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
}

export const permissionsCache: PermissionsCacheHolder = {
  permissions: null,
  roleName: null,
  promise: null,
};

export function clearPermissionsCache(): void {
  permissionsCache.permissions = null;
  permissionsCache.roleName = null;
  permissionsCache.promise = null;
}
