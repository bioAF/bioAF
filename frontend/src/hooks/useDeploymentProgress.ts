"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { useResource } from "@/hooks/useResource";

const PROGRESS_PATH = "/api/v1/infrastructure/stack/deploy/progress";

export interface DeploymentProgress {
  active: boolean;
  status: string | null;
  phase: string | null;
  resources_completed: number;
  resources_total: number;
  completed_resources: string[];
  planned_resources: string[];
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
  planned_resources: [],
  error_message: null,
  run_id: null,
};

/**
 * Poll the deploy progress endpoint. Each read starts after the previous one settles; a
 * refetch supersedes a read in flight; disabling drops any outstanding read.
 *
 * `failed` says the latest read failed. The progress shown is then the last known state, not
 * "no deployment": a failed read is never presented as an idle or finished stack.
 *
 * @param enabled  Pass `true` to start polling. Set to `false` to pause.
 * @param intervalMs  Milliseconds between one read settling and the next (default 4000).
 */
export function useDeploymentProgress(
  enabled: boolean,
  intervalMs = 4000,
) {
  const resource = useResource(
    enabled ? PROGRESS_PATH : null,
    useCallback(
      (signal: AbortSignal) => api.get<DeploymentProgress>(PROGRESS_PATH, { signal }),
      [],
    ),
    {
      poll: intervalMs,
      onError: (e) => logError("reading deployment progress", e),
    },
  );

  return {
    ...(resource.data ?? EMPTY),
    loading: resource.loading,
    failed: resource.error !== null,
    refetch: resource.refresh,
  };
}
