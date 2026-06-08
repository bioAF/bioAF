"use client";

import { useEffect, useState, useCallback } from "react";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";

// The active backend's capability flags, mirroring the backend
// ProviderCapabilities model (app/adapters/capabilities.py). A flag is True
// only when a real implementation backs it; the UI hides or degrades a control
// whose capability is False so swapping to a backend that lacks it (SLURM/NFS)
// never presents a dead button. Capability (can the backend ever) is distinct
// from availability (is the component provisioned now); this hook is about
// capability only.
export type CapabilityFlag =
  | "cost_estimation"
  | "autoscaling"
  | "ssh_exec"
  | "spot_retry"
  | "job_report"
  | "signed_url_upload"
  | "storage_tier_metrics"
  | "notebooks"
  | "cellxgene"
  | "work_nodes"
  | "messaging"
  | "billing";

export type Capabilities = Record<CapabilityFlag, boolean>;

export const CAPABILITY_FLAGS: CapabilityFlag[] = [
  "cost_estimation",
  "autoscaling",
  "ssh_exec",
  "spot_retry",
  "job_report",
  "signed_url_upload",
  "storage_tier_metrics",
  "notebooks",
  "cellxgene",
  "work_nodes",
  "messaging",
  "billing",
];

// Fail-safe default: assume nothing is supported. Hiding an optional control is
// safe; showing one that 500s on use is the bug we are preventing.
export const MINIMAL_CAPABILITIES: Capabilities = CAPABILITY_FLAGS.reduce(
  (acc, flag) => {
    acc[flag] = false;
    return acc;
  },
  {} as Capabilities,
);

interface BootstrapStatusWithCapabilities {
  capabilities?: Partial<Record<CapabilityFlag, boolean>>;
}

let cachedCapabilities: Capabilities | null = null;
let fetchPromise: Promise<void> | null = null;

function normalize(
  raw: Partial<Record<CapabilityFlag, boolean>> | undefined,
): Capabilities {
  if (!raw) return { ...MINIMAL_CAPABILITIES };
  return CAPABILITY_FLAGS.reduce((acc, flag) => {
    acc[flag] = raw[flag] === true;
    return acc;
  }, {} as Capabilities);
}

export function clearCapabilitiesCache(): void {
  cachedCapabilities = null;
  fetchPromise = null;
}

export function useCapabilities() {
  const [capabilities, setCapabilities] = useState<Capabilities>(
    cachedCapabilities ?? { ...MINIMAL_CAPABILITIES },
  );
  const [loading, setLoading] = useState(!cachedCapabilities);

  useEffect(() => {
    // Capabilities are exposed to authenticated callers (the same gating as the
    // rest of the bootstrap-status deployment detail). Before auth, fail safe to
    // the minimal set; no gated control renders pre-login anyway.
    if (!isAuthenticated()) {
      setCapabilities({ ...MINIMAL_CAPABILITIES });
      setLoading(false);
      return;
    }

    if (cachedCapabilities) {
      setCapabilities(cachedCapabilities);
      setLoading(false);
      return;
    }

    if (!fetchPromise) {
      fetchPromise = api
        .get<BootstrapStatusWithCapabilities>("/api/bootstrap/status")
        .then((status) => {
          cachedCapabilities = normalize(status.capabilities);
        })
        .catch(() => {
          cachedCapabilities = { ...MINIMAL_CAPABILITIES };
        });
    }

    fetchPromise.then(() => {
      setCapabilities(cachedCapabilities!);
      setLoading(false);
    });
  }, []);

  const has = useCallback(
    (flag: CapabilityFlag): boolean => capabilities[flag] === true,
    [capabilities],
  );

  return { has, capabilities, loading };
}
