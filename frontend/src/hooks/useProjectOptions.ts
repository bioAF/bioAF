"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  DEFAULT_PROJECTS_PATH,
  projectOptionsFrom,
  projectOptionsFromError,
  type ProjectOptions,
} from "@/lib/projectOptions";
import type { ProjectListResponse } from "@/lib/types";

export type ProjectOptionsStatus = ProjectOptions["status"] | "loading";

/** Stable, so a caller can key an effect or memo on the list while it loads. */
const NO_PROJECTS: ProjectOptions["projects"] = [];

interface Options {
  path?: string;
  /** A picker inside a closed modal has nothing to read yet. */
  enabled?: boolean;
}

/**
 * The project list for a picker that reports its own state. A retry keeps the
 * current status until it has an answer, so whatever the picker fell back to
 * stays in place while the read is in flight.
 */
export function useProjectOptions({ path, enabled = true }: Options = {}) {
  const [options, setOptions] = useState<ProjectOptions | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setOptions(null);
      return;
    }
    let stale = false;
    api.get<ProjectListResponse>(path ?? DEFAULT_PROJECTS_PATH).then(
      (data) => {
        if (!stale) setOptions(projectOptionsFrom(data));
      },
      (error) => {
        if (!stale) setOptions(projectOptionsFromError(error));
      },
    );
    return () => {
      stale = true;
    };
  }, [path, enabled, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const status: ProjectOptionsStatus = options?.status ?? "loading";

  return {
    status,
    projects: options?.projects ?? NO_PROJECTS,
    /** The list did not arrive, so nothing may wait on a project being chosen. */
    unavailable: status === "denied" || status === "failed",
    retry,
  };
}
