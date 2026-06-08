// Leaf module for the BAL capabilities cache. It imports nothing from the app
// (no api client, no auth) so that api.ts can clear the cache on auth changes
// without creating an import cycle (api.ts -> useCapabilities -> api.ts would
// re-enter api.ts during module init and trip an ApiError TDZ error under the
// settings tests' jest.requireActual mock). useCapabilities reads and writes the
// shared holder here.

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

export function normalizeCapabilities(
  raw: Partial<Record<CapabilityFlag, boolean>> | undefined,
): Capabilities {
  if (!raw) return { ...MINIMAL_CAPABILITIES };
  return CAPABILITY_FLAGS.reduce((acc, flag) => {
    acc[flag] = raw[flag] === true;
    return acc;
  }, {} as Capabilities);
}

interface CapabilitiesCacheHolder {
  value: Capabilities | null;
  promise: Promise<void> | null;
}

export const capabilitiesCache: CapabilitiesCacheHolder = {
  value: null,
  promise: null,
};

export function clearCapabilitiesCache(): void {
  capabilitiesCache.value = null;
  capabilitiesCache.promise = null;
}
