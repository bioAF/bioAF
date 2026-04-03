"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface RecoveryItem {
  id: number;
  resource_name: string;
  gcp_project_id: string;
  gcp_zone: string | null;
  stack_uid: string;
  gke_status: string;
  detected_at: string | null;
}

interface RecoveryCheckResponse {
  recoverable: RecoveryItem[];
  provisioning: RecoveryItem[];
  dead: RecoveryItem[];
}

type ModalState =
  | "loading"
  | "recoverable"
  | "provisioning"
  | "none";

interface Props {
  open: boolean;
  onClose: () => void;
  onRecovered: () => void;
  onStartFresh: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DeployRecoveryModal({
  open,
  onClose,
  onRecovered,
  onStartFresh,
}: Props) {
  const [state, setState] = useState<ModalState>("loading");
  const [recoverable, setRecoverable] = useState<RecoveryItem[]>([]);
  const [provisioning, setProvisioning] = useState<RecoveryItem[]>([]);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runCheck = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const data = await api.get<RecoveryCheckResponse>(
        "/api/v1/infrastructure/orphaned-resources/recovery-check",
      );
      setRecoverable(data.recoverable);
      setProvisioning(data.provisioning);

      // Auto-clean dead orphans in the background
      if (data.dead.length > 0) {
        api.post("/api/v1/infrastructure/orphaned-resources/cleanup-all").catch(() => {
          // Best-effort cleanup
        });
      }

      if (data.recoverable.length > 0) {
        setState("recoverable");
      } else if (data.provisioning.length > 0) {
        setState("provisioning");
      } else {
        // Nothing to recover or wait on -- close and allow normal flow
        setState("none");
      }
    } catch {
      setError("Could not check deployment status. Try again in a moment.");
      setState("none");
    }
  }, []);

  useEffect(() => {
    if (open) {
      runCheck();
    }
  }, [open, runCheck]);

  const handleRecover = async () => {
    if (recoverable.length === 0) return;
    setActionLoading(true);
    setError(null);
    try {
      await api.post(
        `/api/v1/infrastructure/orphaned-resources/${recoverable[0].id}/adopt`,
      );
      onRecovered();
    } catch {
      setError(
        "Recovery failed. You can try again or start a fresh deployment.",
      );
    } finally {
      setActionLoading(false);
    }
  };

  const handleStartFresh = async () => {
    setActionLoading(true);
    setError(null);
    try {
      // Clean up all orphans before starting fresh
      await api.post("/api/v1/infrastructure/orphaned-resources/cleanup-all");
      onStartFresh();
    } catch {
      setError(
        "Could not clean up previous resources. Try again in a moment.",
      );
    } finally {
      setActionLoading(false);
    }
  };

  if (!open) return null;

  // If nothing to show, close immediately
  if (state === "none" && !error) {
    // Use a microtask to avoid updating state during render
    Promise.resolve().then(onClose);
    return null;
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl max-w-lg w-full mx-4 overflow-hidden">
        {/* Loading */}
        {state === "loading" && (
          <div className="p-8 text-center">
            <div className="inline-block h-8 w-8 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin mb-4" />
            <p className="text-sm text-gray-600">
              Checking deployment status...
            </p>
          </div>
        )}

        {/* Recoverable: previous deploy finished successfully */}
        {state === "recoverable" && (
          <>
            <div className="p-6 pb-0">
              <div className="flex items-center gap-3 mb-3">
                <div className="flex items-center justify-center h-10 w-10 rounded-full bg-green-100">
                  <svg className="h-5 w-5 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <h3 className="text-lg font-semibold text-gray-900">
                  Previous deployment found
                </h3>
              </div>
              <p className="text-sm text-gray-600 mb-2">
                Your previous deployment took longer than expected, but it
                finished successfully. We can pick up right where you left off.
              </p>
              <p className="text-xs text-gray-400 mb-4">
                This sometimes happens when Google Cloud is experiencing
                delays in your region.
              </p>
              {error && (
                <p className="text-sm text-red-600 bg-red-50 rounded p-2 mb-4">
                  {error}
                </p>
              )}
            </div>
            <div className="bg-gray-50 px-6 py-4 flex justify-end gap-3">
              <button
                onClick={handleStartFresh}
                disabled={actionLoading}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Start Fresh
              </button>
              <button
                onClick={handleRecover}
                disabled={actionLoading}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {actionLoading ? "Recovering..." : "Resume Deployment"}
              </button>
            </div>
          </>
        )}

        {/* Provisioning: cluster is still being created by GCP */}
        {state === "provisioning" && (
          <>
            <div className="p-6 pb-0">
              <div className="flex items-center gap-3 mb-3">
                <div className="flex items-center justify-center h-10 w-10 rounded-full bg-amber-100">
                  <svg className="h-5 w-5 text-amber-600 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                </div>
                <h3 className="text-lg font-semibold text-gray-900">
                  Deployment still in progress
                </h3>
              </div>
              <p className="text-sm text-gray-600 mb-2">
                Google Cloud is still setting up your compute cluster. This is
                taking longer than usual, likely due to a service delay on
                Google&apos;s side.
              </p>
              <p className="text-sm text-gray-600 mb-2">
                There&apos;s nothing you need to do. Come back later and
                we&apos;ll check again automatically.
              </p>
              <p className="text-xs text-gray-400 mb-4">
                You can check{" "}
                <a
                  href="https://status.cloud.google.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  Google Cloud Status
                </a>{" "}
                for current service health.
              </p>
              {error && (
                <p className="text-sm text-red-600 bg-red-50 rounded p-2 mb-4">
                  {error}
                </p>
              )}
            </div>
            <div className="bg-gray-50 px-6 py-4 flex justify-end gap-3">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Got It
              </button>
              <button
                onClick={runCheck}
                disabled={actionLoading}
                className="px-4 py-2 text-sm font-medium text-blue-700 bg-blue-50 border border-blue-200 rounded-lg hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Check Again
              </button>
            </div>
          </>
        )}

        {/* Error with no state */}
        {state === "none" && error && (
          <>
            <div className="p-6">
              <p className="text-sm text-red-600 bg-red-50 rounded p-2">
                {error}
              </p>
            </div>
            <div className="bg-gray-50 px-6 py-4 flex justify-end">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
