"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
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

export function usePermissions() {
  const router = useRouter();
  const [permissions, setPermissions] = useState<Set<string>>(
    permissionsCache.permissions ?? new Set(),
  );
  const [roleName, setRoleName] = useState<string>(permissionsCache.roleName ?? "");
  const [loading, setLoading] = useState(!permissionsCache.permissions);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }

    if (permissionsCache.permissions) {
      setPermissions(permissionsCache.permissions);
      setRoleName(permissionsCache.roleName ?? "");
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
        })
        .catch(() => {
          permissionsCache.permissions = new Set();
          permissionsCache.roleName = "";
        });
    }

    permissionsCache.promise.then(() => {
      setPermissions(permissionsCache.permissions!);
      setRoleName(permissionsCache.roleName ?? "");
      setLoading(false);
    });
  }, [router]);

  const canAccess = useCallback(
    (resource: string, action: string): boolean => {
      return permissions.has(permKey(resource, action));
    },
    [permissions],
  );

  return { canAccess, roleName, loading, permissions };
}
