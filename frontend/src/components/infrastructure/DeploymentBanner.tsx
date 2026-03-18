"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface TerraformStatus {
  terraform_initialized: boolean;
  gcp_credentials_configured: boolean;
  active_run_id: number | null;
  active_run_status: string | null;
}

const POLL_INTERVAL_MS = 10_000;

export function DeploymentBanner() {
  const [deploying, setDeploying] = useState(false);
  const [previouslyDeploying, setPreviouslyDeploying] = useState(false);
  const [showToast, setShowToast] = useState(false);

  const checkStatus = useCallback(async () => {
    try {
      const status = await api.get<TerraformStatus>(
        "/api/v1/infrastructure/terraform/status",
      );
      const isActive = status.active_run_id !== null;

      if (previouslyDeploying && !isActive) {
        // Deployment just finished
        setDeploying(false);
        setShowToast(true);
        setPreviouslyDeploying(false);
        // Auto-dismiss toast after 10 seconds
        setTimeout(() => setShowToast(false), 10_000);
      } else {
        setDeploying(isActive);
        setPreviouslyDeploying(isActive);
      }
    } catch {
      // Silently ignore -- user may not be admin or not logged in
    }
  }, [previouslyDeploying]);

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [checkStatus]);

  return (
    <>
      {deploying && (
        <div
          data-testid="deployment-banner"
          className="bg-amber-50 border-b border-amber-300 px-6 py-2 flex items-center justify-between"
        >
          <div className="flex items-center gap-2">
            <span className="inline-block h-2 w-2 bg-amber-500 rounded-full animate-pulse" />
            <span className="text-sm text-amber-800 font-medium">
              Infrastructure is deploying. Please do not start any pipeline runs yet.
            </span>
          </div>
          <Link
            href="/infrastructure/components"
            className="text-sm text-amber-700 underline hover:text-amber-900"
          >
            View progress
          </Link>
        </div>
      )}

      {showToast && (
        <div
          data-testid="deployment-toast"
          className="fixed top-4 right-4 z-50 bg-green-600 text-white px-4 py-3 rounded-lg shadow-lg flex items-center gap-3"
        >
          <span className="text-sm font-medium">
            Compute stack deployed. You can now select components.
          </span>
          <Link
            href="/infrastructure/components"
            className="text-sm underline text-green-100 hover:text-white"
          >
            Go to Components
          </Link>
          <button
            onClick={() => setShowToast(false)}
            className="text-green-200 hover:text-white ml-2"
            aria-label="Dismiss"
          >
            x
          </button>
        </div>
      )}
    </>
  );
}
