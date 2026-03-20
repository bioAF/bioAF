"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";

interface OrphanedResource {
  id: number;
  resource_type: string;
  resource_name: string;
  gcp_project_id: string;
  gcp_zone: string | null;
  stack_uid: string;
  status: string;
  error_message: string | null;
  detected_at: string;
  resolved_at: string | null;
}

interface OrphanedResourceListResponse {
  items: OrphanedResource[];
  total: number;
}

const STATUS_BADGE: Record<string, string> = {
  detected: "text-amber-700 bg-amber-50",
  cleaning: "text-blue-700 bg-blue-50",
  cleaned: "text-green-700 bg-green-50",
  dismissed: "text-gray-700 bg-gray-50",
  failed: "text-red-700 bg-red-50",
};

const RESOURCE_LABELS: Record<string, string> = {
  gke_cluster: "GKE Cluster",
  gcs_bucket: "GCS Bucket",
};

export function OrphanedResourcesCard() {
  const [resources, setResources] = useState<OrphanedResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionInProgress, setActionInProgress] = useState<number | null>(null);

  const fetchResources = useCallback(async () => {
    try {
      const data = await api.get<OrphanedResourceListResponse>(
        "/api/v1/infrastructure/orphaned-resources"
      );
      // Only show unresolved
      setResources(
        data.items.filter((r) => r.status === "detected" || r.status === "failed")
      );
    } catch {
      // Silently ignore -- card just won't render
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchResources();
  }, [fetchResources]);

  const handleCleanup = async (id: number) => {
    setActionInProgress(id);
    try {
      await api.post(`/api/v1/infrastructure/orphaned-resources/${id}/cleanup`);
      await fetchResources();
    } catch {
      await fetchResources();
    } finally {
      setActionInProgress(null);
    }
  };

  const handleDismiss = async (id: number) => {
    setActionInProgress(id);
    try {
      await api.post(`/api/v1/infrastructure/orphaned-resources/${id}/dismiss`);
      await fetchResources();
    } catch {
      await fetchResources();
    } finally {
      setActionInProgress(null);
    }
  };

  if (loading || resources.length === 0) return null;

  return (
    <div className="mb-6 rounded-lg border border-amber-300 bg-amber-50 p-4">
      <h3 className="text-sm font-semibold text-amber-800 mb-3">
        Orphaned Resources
      </h3>
      <p className="text-xs text-amber-700 mb-3">
        These GCP resources were left behind by a failed deployment and may still
        be accruing costs. Clean them up or dismiss if already handled.
      </p>
      <div className="space-y-2">
        {resources.map((r) => (
          <div
            key={r.id}
            className="flex items-center justify-between bg-white rounded border border-amber-200 px-3 py-2"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-gray-500">
                  {RESOURCE_LABELS[r.resource_type] ?? r.resource_type}
                </span>
                <span
                  className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[r.status] ?? "text-gray-700 bg-gray-50"}`}
                >
                  {r.status}
                </span>
              </div>
              <p className="text-sm font-mono text-gray-900 truncate">
                {r.resource_name}
              </p>
              {r.error_message && (
                <p className="text-xs text-red-600 mt-0.5 truncate">
                  {r.error_message}
                </p>
              )}
              <p className="text-xs text-gray-500 mt-0.5">
                {r.gcp_project_id}
                {r.gcp_zone ? ` / ${r.gcp_zone}` : ""} &middot; Detected{" "}
                {new Date(r.detected_at).toLocaleDateString()}
              </p>
            </div>
            <div className="flex gap-2 ml-3 flex-shrink-0">
              <button
                onClick={() => handleCleanup(r.id)}
                disabled={actionInProgress !== null}
                className="px-3 py-1.5 text-xs font-medium rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {actionInProgress === r.id ? "Cleaning..." : "Clean Up"}
              </button>
              <button
                onClick={() => handleDismiss(r.id)}
                disabled={actionInProgress !== null}
                className="px-3 py-1.5 text-xs font-medium rounded bg-gray-200 text-gray-700 hover:bg-gray-300 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Dismiss
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
