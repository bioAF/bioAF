"use client";

import { useEffect, useState } from "react";
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

export function useComponents() {
  const [components, setComponents] = useState<ComponentState[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchComponents = async () => {
    try {
      const data = await api.get<StackComponentsResponse>(
        "/api/v1/infrastructure/stack/components",
      );
      setComponents(
        data.components.map((c) => ({
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
        })),
      );
    } catch {
      // handled by api client
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchComponents();
  }, []);

  return { components, loading, refetch: fetchComponents };
}
