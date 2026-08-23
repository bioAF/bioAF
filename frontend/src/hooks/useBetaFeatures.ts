"use client";

import { useEffect, useState } from "react";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";

// Beta feature flags (spec-07). `flags` carries per-feature enablement so the nav can hide a beta
// entry (e.g. Validation Studies) until an admin turns its flag on.
//
// There used to be an `available` field gating the whole Beta Features surface on the instance being
// bioAF-operated (an admin with a @bioaf.co email). It was removed: it made every beta feature
// internal-only on any instance bioAF does not staff.
export interface BetaFeaturesState {
  flags: Record<string, boolean>;
}

// Default-DENY: while loading, unauthenticated, or on error, hide everything beta. A hidden feature
// must never flash into view before the real state resolves.
const DENY: BetaFeaturesState = { flags: {} };

export function useBetaFeatures() {
  const [state, setState] = useState<BetaFeaturesState>(DENY);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isAuthenticated()) {
      setState(DENY);
      setLoading(false);
      return;
    }
    let alive = true;
    api
      .get<BetaFeaturesState>("/api/beta-features")
      .then((s) => {
        if (alive) setState({ flags: s.flags ?? {} });
      })
      .catch(() => {
        if (alive) setState(DENY);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  return { flags: state.flags, loading };
}
