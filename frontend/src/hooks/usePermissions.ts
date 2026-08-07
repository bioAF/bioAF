"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { permissionsCache, clearPermissionsCache } from "@/hooks/permissionsCache";

// Re-exported so existing importers (Header, api.ts's leaf import, tests that mock
// this module) keep working. The cache state itself lives in the leaf
// permissionsCache module to avoid the api.ts <-> usePermissions import cycle.
export { clearPermissionsCache };

interface Permission {
  resource: string;
  action: string;
}

interface MeResponse {
  id: number;
  email: string;
  name: string | null;
  role_id: number;
  role_name: string;
  organization_id: number;
  status: string;
  permissions: Permission[];
}

function permKey(resource: string, action: string): string {
  return `${resource}:${action}`;
}

/**
 * What this user is allowed to do.
 *
 * `failed` exists because the old `.catch(() => { permissions = new Set() })`
 * turned an outage into a fully navigable app in which the user could do
 * nothing. Measured on the deployed app 2026-08-07: a 500 on `/api/auth/me`
 * collapsed the sidebar to a single item and the dashboard read "Your dashboard
 * has no widgets. Add widgets" -- a failed load presented as the user's own
 * preference. Caching the empty set made it permanent for the tab.
 *
 * Note what did NOT change: an unknown permission still denies. Granting what we
 * cannot verify would be a security defect. The fix is that callers can now tell
 * the difference and say so.
 */
export function usePermissions() {
  const router = useRouter();
  const [permissions, setPermissions] = useState<Set<string>>(
    permissionsCache.permissions ?? new Set(),
  );
  const [roleName, setRoleName] = useState<string>(permissionsCache.roleName ?? "");
  const [loading, setLoading] = useState(!permissionsCache.permissions);
  const [failed, setFailed] = useState(permissionsCache.failed);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }

    if (permissionsCache.permissions) {
      setPermissions(permissionsCache.permissions);
      setRoleName(permissionsCache.roleName ?? "");
      setFailed(false);
      setLoading(false);
      return;
    }

    if (!permissionsCache.promise) {
      permissionsCache.promise = api
        .get<MeResponse>("/api/auth/me")
        .then((me) => {
          const permSet = new Set<string>();
          for (const p of me.permissions) {
            permSet.add(permKey(p.resource, p.action));
          }
          permissionsCache.permissions = permSet;
          permissionsCache.roleName = me.role_name;
          permissionsCache.failed = false;
        })
        .catch((err) => {
          logError("loading your permissions", err);
          permissionsCache.failed = true;
          // Leave `permissions` null and drop the shared promise, so the next
          // mount retries rather than inheriting this failure.
          permissionsCache.promise = null;
        });
    }

    permissionsCache.promise.then(() => {
      setPermissions(permissionsCache.permissions ?? new Set());
      setRoleName(permissionsCache.roleName ?? "");
      setFailed(permissionsCache.failed);
      setLoading(false);
    });
  }, [router]);

  const canAccess = useCallback(
    (resource: string, action: string): boolean => {
      return permissions.has(permKey(resource, action));
    },
    [permissions],
  );

  return { canAccess, roleName, loading, permissions, failed };
}
