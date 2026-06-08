"use client";

import { useEffect, useState, useCallback } from "react";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  capabilitiesCache,
  normalizeCapabilities,
  MINIMAL_CAPABILITIES,
  type CapabilityFlag,
} from "./capabilitiesCache";

// Re-export the cache primitives so consumers keep a single import surface
// ("@/hooks/useCapabilities"). The cache itself lives in a leaf module to avoid
// an import cycle with the api client (see capabilitiesCache.ts).
export {
  clearCapabilitiesCache,
  CAPABILITY_FLAGS,
  MINIMAL_CAPABILITIES,
} from "./capabilitiesCache";
export type { CapabilityFlag, Capabilities } from "./capabilitiesCache";

// The active backend's capability flags mirror the backend ProviderCapabilities
// model (app/adapters/capabilities.py). A flag is True only when a real
// implementation backs it; the UI hides or degrades a control whose capability
// is False so swapping to a backend that lacks it (SLURM/NFS) never presents a
// dead button. Capability (can the backend ever) is distinct from availability
// (is the component provisioned now); this hook is about capability only.
interface BootstrapStatusWithCapabilities {
  capabilities?: Partial<Record<CapabilityFlag, boolean>>;
}

export function useCapabilities() {
  const [capabilities, setCapabilities] = useState(
    capabilitiesCache.value ?? { ...MINIMAL_CAPABILITIES },
  );
  const [loading, setLoading] = useState(!capabilitiesCache.value);

  useEffect(() => {
    // Capabilities are exposed to authenticated callers (the same gating as the
    // rest of the bootstrap-status deployment detail). Before auth, fail safe to
    // the minimal set; no gated control renders pre-login anyway.
    if (!isAuthenticated()) {
      setCapabilities({ ...MINIMAL_CAPABILITIES });
      setLoading(false);
      return;
    }

    if (capabilitiesCache.value) {
      setCapabilities(capabilitiesCache.value);
      setLoading(false);
      return;
    }

    if (!capabilitiesCache.promise) {
      capabilitiesCache.promise = api
        .get<BootstrapStatusWithCapabilities>("/api/bootstrap/status")
        .then((status) => {
          capabilitiesCache.value = normalizeCapabilities(status.capabilities);
        })
        .catch(() => {
          capabilitiesCache.value = { ...MINIMAL_CAPABILITIES };
        });
    }

    capabilitiesCache.promise.then(() => {
      setCapabilities(capabilitiesCache.value!);
      setLoading(false);
    });
  }, []);

  const has = useCallback(
    (flag: CapabilityFlag): boolean => capabilities[flag] === true,
    [capabilities],
  );

  return { has, capabilities, loading };
}
