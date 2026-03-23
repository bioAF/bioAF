"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";

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

let cachedPermissions: Set<string> | null = null;
let cachedRoleName: string | null = null;
let fetchPromise: Promise<void> | null = null;

function permKey(resource: string, action: string): string {
  return `${resource}:${action}`;
}

export function clearPermissionsCache(): void {
  cachedPermissions = null;
  cachedRoleName = null;
  fetchPromise = null;
}

export function usePermissions() {
  const router = useRouter();
  const [permissions, setPermissions] = useState<Set<string>>(
    cachedPermissions ?? new Set(),
  );
  const [roleName, setRoleName] = useState<string>(cachedRoleName ?? "");
  const [loading, setLoading] = useState(!cachedPermissions);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }

    if (cachedPermissions) {
      setPermissions(cachedPermissions);
      setRoleName(cachedRoleName ?? "");
      setLoading(false);
      return;
    }

    if (!fetchPromise) {
      fetchPromise = api
        .get<MeResponse>("/api/auth/me")
        .then((me) => {
          const permSet = new Set<string>();
          for (const p of me.permissions) {
            permSet.add(permKey(p.resource, p.action));
          }
          cachedPermissions = permSet;
          cachedRoleName = me.role_name;
        })
        .catch(() => {
          cachedPermissions = new Set();
          cachedRoleName = "";
        });
    }

    fetchPromise.then(() => {
      setPermissions(cachedPermissions!);
      setRoleName(cachedRoleName ?? "");
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
