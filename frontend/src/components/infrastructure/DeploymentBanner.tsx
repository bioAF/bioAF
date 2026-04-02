"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface TerraformStatus {
  terraform_initialized: boolean;
  gcp_credentials_configured: boolean;
  active_run_id: number | null;
  active_run_status: string | null;
  last_completed_module: string | null;
}

const POLL_INTERVAL_MS = 10_000;

const TOAST_MESSAGES: Record<string, { text: string; link: string; linkText: string }> = {
  storage: {
    text: "Storage deployed successfully on Google GCS. You can now upload files.",
    link: "/infrastructure/components",
    linkText: "Go to Components",
  },
  compute: {
    text: "Compute cluster successfully deployed on Google GKE. You can now select components and run pipelines.",
    link: "/infrastructure/components",
    linkText: "Go to Components",
  },
  default: {
    text: "Infrastructure operation complete.",
    link: "/infrastructure/components",
    linkText: "Go to Components",
  },
};

export function DeploymentBanner() {
  const [deploying, setDeploying] = useState(false);
  const [previouslyDeploying, setPreviouslyDeploying] = useState(false);
  const [toast, setToast] = useState<{ text: string; link: string; linkText: string } | null>(null);
  const lastSeenModuleRef = useRef<string | null>(null);

  const checkStatus = useCallback(async () => {
    try {
      const status = await api.get<TerraformStatus>(
        "/api/v1/infrastructure/terraform/status",
      );
      const isActive = status.active_run_id !== null;

      if (previouslyDeploying && !isActive) {
        // A run just finished -- show a phase-specific toast
        const completedModule = status.last_completed_module;
        // Only show the toast if the completed module changed (avoids
        // re-showing the same toast on every poll after completion).
        if (completedModule !== lastSeenModuleRef.current) {
          lastSeenModuleRef.current = completedModule;
          const message = TOAST_MESSAGES[completedModule ?? "default"] ?? TOAST_MESSAGES.default;
          setToast(message);
          setTimeout(() => setToast(null), 10_000);
        }
        setDeploying(false);
        setPreviouslyDeploying(false);
      } else {
        setDeploying(isActive);
        setPreviouslyDeploying(isActive);
        if (isActive) {
          // Track current module so we detect the transition
          lastSeenModuleRef.current = status.last_completed_module;
        }
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

      {toast && (
        <div
          data-testid="deployment-toast"
          className="fixed top-4 right-4 z-50 bg-green-600 text-white px-4 py-3 rounded-lg shadow-lg flex items-center gap-3 max-w-md"
        >
          <span className="text-sm font-medium">
            {toast.text}
          </span>
          <Link
            href={toast.link}
            className="text-sm underline text-green-100 hover:text-white shrink-0"
          >
            {toast.linkText}
          </Link>
          <button
            onClick={() => setToast(null)}
            className="text-green-200 hover:text-white ml-2 shrink-0"
            aria-label="Dismiss"
          >
            x
          </button>
        </div>
      )}
    </>
  );
}
