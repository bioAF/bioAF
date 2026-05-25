"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { defaultLayoutForRole } from "@/components/dashboard/registry";

interface LayoutWidget {
  key: string;
  settings?: Record<string, unknown>;
}

interface LayoutResponse {
  configured: boolean;
  widgets: LayoutWidget[];
}

/**
 * Loads the user's saved dashboard layout. When the user has never configured
 * one (configured=false), seeds the role default. Exposes an optimistic `save`.
 *
 * @param roleName the current user's role (drives the default seed)
 * @param ready    wait until permissions/role are loaded before fetching
 */
export function useDashboardLayout(roleName: string, ready: boolean) {
  const [keys, setKeys] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;

    api
      .get<LayoutResponse>("/api/dashboard/layout")
      .then((res) => {
        if (cancelled) return;
        setKeys(
          res.configured ? res.widgets.map((w) => w.key) : defaultLayoutForRole(roleName),
        );
      })
      .catch(() => {
        if (!cancelled) setKeys(defaultLayoutForRole(roleName));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ready, roleName]);

  const save = useCallback(async (nextKeys: string[]) => {
    setKeys(nextKeys); // optimistic
    setSaving(true);
    try {
      await api.put("/api/dashboard/layout", {
        widgets: nextKeys.map((key) => ({ key, settings: {} })),
      });
    } finally {
      setSaving(false);
    }
  }, []);

  return { keys, loading, saving, save };
}
