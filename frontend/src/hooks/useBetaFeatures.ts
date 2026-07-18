"use client";

import { useEffect, useState } from "react";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";

// Beta feature flags (spec-07). `available` gates whether the Settings > Beta Features surface is
// exposed at all (true only on a bioAF-operated instance); `flags` carries per-feature enablement so
// the nav can hide a beta entry (e.g. Validation Studies) until its flag is on.
export interface BetaFeaturesState {
  available: boolean;
  flags: Record<string, boolean>;
}

// Default-DENY: while loading, unauthenticated, or on error, hide everything beta. A hidden feature
// must never flash into view before the real state resolves.
const DENY: BetaFeaturesState = { available: false, flags: {} };

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
        if (alive) setState({ available: !!s.available, flags: s.flags ?? {} });
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

  return { available: state.available, flags: state.flags, loading };
}
