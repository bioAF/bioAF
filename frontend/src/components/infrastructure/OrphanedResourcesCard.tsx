"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { statusBadgeClass } from "@/lib/statusStyles";
import { useConfirm } from "@/hooks/useConfirm";
import { useToast } from "@/components/shared/Toast";

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

// Resource-type labels keyed by the backend's orphaned-resource type strings.
// GCP emits gke_cluster / gcs_bucket; the AWS equivalents are listed so an AWS
// install labels its orphans correctly once AWS orphan-detection lands.
const RESOURCE_LABELS: Record<string, string> = {
  gke_cluster: "GKE Cluster",
  gcs_bucket: "GCS Bucket",
  eks_cluster: "EKS Cluster",
  s3_bucket: "S3 Bucket",
};

/** Resource types where "Clean Up" destroys stored data, not just compute. */
const DESTROYS_DATA = new Set(["gcs_bucket", "s3_bucket"]);

export function OrphanedResourcesCard() {
  const confirm = useConfirm();
  const toast = useToast();
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
    } catch (err) {
      // Was "Silently ignore -- card just won't render". These resources cost
      // money every hour they exist, so hiding the card on a failed load hides a
      // bill. The card still declines to render (there is nothing to list) but
      // the failure now reaches the log instead of vanishing.
      logError("loading orphaned cloud resources", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchResources();
  }, [fetchResources]);

  const handleCleanup = async (id: number) => {
    const resource = resources.find((r) => r.id === id);
    const label = resource ? RESOURCE_LABELS[resource.resource_type] ?? resource.resource_type : "resource";
    const destroysData = resource ? DESTROYS_DATA.has(resource.resource_type) : false;

    // This button had no gate at all. On this same page, destroying object
    // storage is gated by a checkbox plus a typed "delete my data"; a bucket
    // reached through this card was one red click. Same act, and the weak gate
    // reads as safe precisely because the strong one exists.
    const ok = await confirm({
      title: destroysData ? `Permanently delete this ${label}?` : `Delete this ${label}?`,
      message: destroysData ? (
        <>
          <p>
            <span className="font-medium">{resource?.resource_name ?? `#${id}`}</span> and{" "}
            <span className="font-medium">everything stored in it</span> will be permanently deleted.
            That includes any raw sample data, pipeline outputs and results it holds.
          </p>
          <p>This cannot be undone, and bioAF cannot recover the contents afterwards.</p>
        </>
      ) : (
        <>
          <p>
            <span className="font-medium">{resource?.resource_name ?? `#${id}`}</span> will be
            deleted from {resource?.gcp_project_id ?? "your cloud project"}. This cannot be undone.
          </p>
          <p>No data stored in bioAF is affected.</p>
        </>
      ),
      confirmLabel: destroysData ? "Delete permanently" : "Delete",
      variant: "danger",
      requirePhrase: destroysData ? "delete my data" : undefined,
    });
    if (!ok) return;

    setActionInProgress(id);
    try {
      await api.post(`/api/v1/infrastructure/orphaned-resources/${id}/cleanup`);
      await fetchResources();
    } catch (err) {
      logError(`cleaning up orphaned ${label} ${id}`, err);
      toast.error(`That ${label} could not be deleted. The technical detail is in the application logs.`);
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
        These cloud resources were left behind by a failed deployment and may still
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
                  className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${statusBadgeClass("orphanedResource", r.status)}`}
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
