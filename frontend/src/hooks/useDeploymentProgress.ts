"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";

export interface DeploymentProgress {
  active: boolean;
  status: string | null;
  phase: string | null;
  resources_completed: number;
  resources_total: number;
  completed_resources: string[];
  error_message: string | null;
  run_id: number | null;
}

const EMPTY: DeploymentProgress = {
  active: false,
  status: null,
  phase: null,
  resources_completed: 0,
  resources_total: 0,
  completed_resources: [],
  error_message: null,
  run_id: null,
};

/**
 * Poll the deploy progress endpoint at a fixed interval.
 *
 * @param enabled  Pass `true` to start polling. Set to `false` to pause.
 * @param intervalMs  Polling interval in milliseconds (default 4000).
 */
export function useDeploymentProgress(
  enabled: boolean,
  intervalMs = 4000,
) {
  const [progress, setProgress] = useState<DeploymentProgress>(EMPTY);
  const [loading, setLoading] = useState(false);
  const mountedRef = useRef(true);

  const poll = useCallback(async () => {
    try {
      const data = await api.get<DeploymentProgress>(
        "/api/v1/infrastructure/stack/deploy/progress",
      );
      if (mountedRef.current) {
        setProgress(data);
      }
    } catch {
      // Silently ignore -- user may not have permission or server is down
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    setLoading(true);
    poll();
    const interval = setInterval(poll, intervalMs);
    return () => clearInterval(interval);
  }, [enabled, intervalMs, poll]);

  return { ...progress, loading, refetch: poll };
}
