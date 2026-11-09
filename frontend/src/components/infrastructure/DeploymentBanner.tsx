"use client";

import { useState, useCallback, useRef } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { isPermissionDenied } from "@/lib/projectOptions";

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
  const [toast, setToast] = useState<{ text: string; link: string; linkText: string } | null>(null);
  const previouslyDeployingRef = useRef(false);
  const lastSeenModuleRef = useRef<string | null>(null);

  // A run finishing is announced once. The previous answer's state is kept only to detect that
  // transition; whether the banner shows is derived from the current answer below.
  const onStatus = useCallback((status: TerraformStatus) => {
    const isActive = status.active_run_id !== null;

    if (previouslyDeployingRef.current && !isActive) {
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
      previouslyDeployingRef.current = false;
    } else {
      previouslyDeployingRef.current = isActive;
      if (isActive) {
        // Track current module so we detect the transition
        lastSeenModuleRef.current = status.last_completed_module;
      }
    }
  }, []);

  // Each read starts after the previous one settles. A failed read is not a finished run: it
  // changes nothing but the stale note. A 403 is expected (the endpoint needs
  // infrastructure:change_status, so every non-admin gets it) and is not re-polled.
  const statusRead = useResource(
    "terraform-status",
    useCallback(
      (signal: AbortSignal) =>
        api.get<TerraformStatus>("/api/v1/infrastructure/terraform/status", { signal }),
      [],
    ),
    { poll: POLL_INTERVAL_MS, expected: isPermissionDenied, onSuccess: onStatus },
  );
  // The last answer stays during a failed refresh, so a running deployment keeps its banner.
  const deploying = statusRead.data ? statusRead.data.active_run_id !== null : false;

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
              {statusRead.stale && " Deployment status could not be refreshed; bioAF keeps checking."}
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
