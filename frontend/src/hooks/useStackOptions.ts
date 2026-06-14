"use client";

import { useEffect, useState } from "react";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";

// One selectable compute+storage stack with provider-appropriate labels. Mirrors
// the backend StackOption (app/schemas/infrastructure.py); the backend is the
// source of truth for which combos are valid/available on the install's cloud.
export interface StackOption {
  compute_stack: string; // "kubernetes" | "slurm"
  storage_backend: string; // "gcs" | "s3" | "nfs"
  label: string; // combined, e.g. "Kubernetes + GCS"
  compute_label: string; // e.g. "Kubernetes (GKE)" / "Kubernetes (EKS)"
  storage_label: string; // e.g. "GCS" / "S3" / "NFS"
  available: boolean; // selectable today (SLURM is not yet)
  recommended: boolean;
}

export interface StackOptionsResponse {
  cloud_provider: string;
  options: StackOption[];
}

// GCP defaults: exactly what /stack-options returns on a GCP install. Used while
// the fetch is in flight or if it fails, so a GCP install renders identical
// labels with no flash of wrong copy.
export const DEFAULT_STACK_OPTIONS: StackOptionsResponse = {
  cloud_provider: "gcp",
  options: [
    {
      compute_stack: "kubernetes",
      storage_backend: "gcs",
      label: "Kubernetes + GCS",
      compute_label: "Kubernetes (GKE)",
      storage_label: "GCS",
      available: true,
      recommended: true,
    },
    {
      compute_stack: "slurm",
      storage_backend: "nfs",
      label: "SLURM + NFS",
      compute_label: "SLURM",
      storage_label: "NFS",
      available: false,
      recommended: false,
    },
  ],
};

/** Module-level cache so navigation doesn't re-fetch or flash loading. */
let cached: StackOptionsResponse | null = null;
let fetchPromise: Promise<void> | null = null;

/** Invalidate the module-level cache so the next render re-fetches. */
export function invalidateStackOptionsCache() {
  cached = null;
  fetchPromise = null;
}

/**
 * The install's stack options (GCP -> GKE+GCS, AWS -> EKS+S3, ...), read from the
 * backend /stack-options endpoint (the same cloud-provider POLICY the BAL uses).
 * Fails safe to GCP defaults pre-auth / on error so GCP behavior is unchanged.
 */
export function useStackOptions() {
  const [data, setData] = useState<StackOptionsResponse>(cached ?? DEFAULT_STACK_OPTIONS);
  const [loading, setLoading] = useState(!cached);

  useEffect(() => {
    if (!isAuthenticated()) {
      setData(DEFAULT_STACK_OPTIONS);
      setLoading(false);
      return;
    }
    if (cached) {
      setData(cached);
      setLoading(false);
      return;
    }
    if (!fetchPromise) {
      fetchPromise = api
        .get<StackOptionsResponse>("/api/v1/infrastructure/stack-options")
        .then((resp) => {
          cached = resp;
        })
        .catch(() => {
          cached = DEFAULT_STACK_OPTIONS;
        });
    }
    fetchPromise.then(() => {
      setData(cached!);
      setLoading(false);
    });
  }, []);

  return {
    cloudProvider: data.cloud_provider,
    options: data.options,
    // The kubernetes (object-store) option is the one the setup UI features as
    // the recommended choice; expose it directly for label rendering.
    kubernetesOption: data.options.find((o) => o.compute_stack === "kubernetes") ?? null,
    loading,
  };
}
