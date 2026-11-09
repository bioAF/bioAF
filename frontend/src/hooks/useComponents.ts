"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { onSessionReset, sessionGeneration } from "@/lib/session";
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
/**
 * Set when the last attempt failed. Deliberately NOT cached alongside
 * `cachedComponents`: a failure must not become the answer for the life of the
 * tab, and every componentGate needs to be able to tell "we could not check"
 * apart from "it is not installed".
 */
let lastFetchFailed = false;

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
  lastFetchFailed = false;
}

// The endpoint requires infrastructure:view, so the answer is the signed-in user's: it ends with
// the session.
onSessionReset(invalidateComponentCache);

/**
 * The installed stack components.
 *
 * `failed` exists because the old `.catch(() => { cachedComponents = [] })`
 * turned an outage into a claim. Measured on the deployed app 2026-08-07: a 500
 * on this one endpoint removed the entire Pipelines section from the sidebar (8
 * nav items instead of 9) with no error anywhere on screen, so the user
 * concluded the feature was not installed. Caching that empty array made it
 * permanent for the tab.
 */
export function useComponents() {
  const [components, setComponents] = useState<ComponentState[]>(
    cachedComponents ?? [],
  );
  const [loading, setLoading] = useState(!cachedComponents);
  const [failed, setFailed] = useState(lastFetchFailed);

  useEffect(() => {
    if (cachedComponents) {
      setComponents(cachedComponents);
      setFailed(false);
      setLoading(false);
      return;
    }

    const generation = sessionGeneration();
    const current = () => generation === sessionGeneration();

    if (!fetchPromise) {
      fetchPromise = api
        .get<StackComponentsResponse>(
          "/api/v1/infrastructure/stack/components",
        )
        .then((data) => {
          if (!current()) return;
          cachedComponents = mapComponents(data);
          lastFetchFailed = false;
        })
        .catch((err) => {
          if (!current()) return;
          logError("loading the installed stack components", err);
          lastFetchFailed = true;
          // Leave the cache null and drop the shared promise so the next mount
          // genuinely retries instead of inheriting this failure.
          fetchPromise = null;
        });
    }

    fetchPromise.then(() => {
      if (!current()) return;
      setComponents(cachedComponents ?? []);
      setFailed(lastFetchFailed);
      setLoading(false);
    });
  }, []);

  const refetch = useCallback(async () => {
    const generation = sessionGeneration();
    try {
      const data = await api.get<StackComponentsResponse>(
        "/api/v1/infrastructure/stack/components",
      );
      if (generation !== sessionGeneration()) return;
      cachedComponents = mapComponents(data);
      lastFetchFailed = false;
      setComponents(cachedComponents);
      setFailed(false);
    } catch (err) {
      // The old comment here claimed the api client handled this. It does not:
      // lib/api.ts only throws.
      if (generation !== sessionGeneration()) return;
      logError("refreshing the installed stack components", err);
      lastFetchFailed = true;
      setFailed(true);
    }
  }, []);

  return { components, loading, failed, refetch };
}
