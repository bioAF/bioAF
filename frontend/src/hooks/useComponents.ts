"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import type { ComponentState } from "@/lib/types";

interface StackComponentsResponse {
  compute_stack: string | null;
  compute_deployed: boolean;
  storage_deployed: boolean;
  components: Array<{
    key: string;
    name: string;
    category: string;
    description: string;
    cost_estimate: string;
    dependencies: string[];
    status: string;
    configurable: boolean;
  }>;
}

/** Module-level cache so navigation doesn't re-fetch or flash loading. */
let cachedComponents: ComponentState[] | null = null;
let fetchPromise: Promise<void> | null = null;

function mapComponents(
  data: StackComponentsResponse,
): ComponentState[] {
  return data.components.map((c) => ({
    key: c.key,
    name: c.name,
    description: c.description,
    category: c.category,
    enabled: c.status === "enabled" || c.status === "provisioning",
    status: c.status,
    config: {},
    dependencies: c.dependencies,
    estimated_monthly_cost: c.cost_estimate,
    updated_at: null,
  }));
}

/** Invalidate the module-level cache so the next render re-fetches. */
export function invalidateComponentCache() {
  cachedComponents = null;
  fetchPromise = null;
}

export function useComponents() {
  const [components, setComponents] = useState<ComponentState[]>(
    cachedComponents ?? [],
  );
  const [loading, setLoading] = useState(!cachedComponents);

  useEffect(() => {
    if (cachedComponents) {
      setComponents(cachedComponents);
      setLoading(false);
      return;
    }

    if (!fetchPromise) {
      fetchPromise = api
        .get<StackComponentsResponse>(
          "/api/v1/infrastructure/stack/components",
        )
        .then((data) => {
          cachedComponents = mapComponents(data);
        })
        .catch(() => {
          cachedComponents = [];
        });
    }

    fetchPromise.then(() => {
      setComponents(cachedComponents!);
      setLoading(false);
    });
  }, []);

  const refetch = useCallback(async () => {
    try {
      const data = await api.get<StackComponentsResponse>(
        "/api/v1/infrastructure/stack/components",
      );
      cachedComponents = mapComponents(data);
      setComponents(cachedComponents);
    } catch {
      // handled by api client
    }
  }, []);

  return { components, loading, refetch };
}
