"use client";

import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/auth";

interface TerraformEvent {
  event_type: string;
  message: string;
  resource_address?: string;
  resources_completed?: number;
  resources_total?: number;
  log_line?: string;
}

interface TerraformProgressModalProps {
  title: string;
  sseUrl: string;
  onComplete: () => void;
  onClose: () => void;
}

type ModalStatus = "connecting" | "running" | "complete" | "error";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Friendly names for Terraform resource types. Biologists don't need to
// know about google_storage_bucket -- they care about "Data storage".
// ---------------------------------------------------------------------------

const FRIENDLY_NAMES: Record<string, string> = {
  // Storage resources
  "google_storage_bucket.ingest": "Ingest data storage",
  "google_storage_bucket.raw": "Raw data storage",
  "google_storage_bucket.working": "Working data storage",
  "google_storage_bucket.results": "Results storage",
  "google_storage_bucket.config_backups": "Configuration backups",
  // Pub/Sub
  "google_pubsub_topic.ingest_events": "Data ingest notifications",
  "google_pubsub_subscription.ingest_worker": "Ingest processing queue",
  "google_pubsub_topic.ingest_dead_letter": "Failed ingest retry queue",
  "google_pubsub_subscription.ingest_dead_letter_sub":
    "Failed ingest retry handler",
  "google_storage_notification.ingest_notification":
    "Automatic file detection",
  "google_pubsub_topic_iam_member.gcs_publisher":
    "Storage notification permissions",
  // Compute resources
  "google_container_cluster.bioaf": "Compute cluster",
  "google_container_node_pool.pipelines": "Pipeline processing nodes",
  "google_container_node_pool.interactive": "Interactive session nodes",
  "google_project_iam_member.gke_storage_access":
    "Cluster storage permissions",
};

type ResourceStatus = "pending" | "in_progress" | "complete";

interface TrackedResource {
  address: string;
  label: string;
  status: ResourceStatus;
}

function friendlyLabel(address: string): string {
  if (FRIENDLY_NAMES[address]) return FRIENDLY_NAMES[address];
  // Fallback: strip provider prefix and humanize
  const parts = address.split(".");
  if (parts.length === 2) {
    return parts[1].replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return address;
}

function StatusBadge({ status }: { status: ResourceStatus }) {
  if (status === "complete") {
    return (
      <span className="text-xs font-medium text-green-600 uppercase tracking-wide">
        Done
      </span>
    );
  }
  if (status === "in_progress") {
    return (
      <span className="text-xs font-medium text-blue-600 uppercase tracking-wide flex items-center gap-1.5">
        <span className="inline-block h-1.5 w-1.5 bg-blue-600 rounded-full animate-pulse" />
        Setting up
      </span>
    );
  }
  return (
    <span className="text-xs text-gray-400 uppercase tracking-wide">
      Queued
    </span>
  );
}

export function TerraformProgressModal({
  title,
  sseUrl,
  onComplete,
  onClose,
}: TerraformProgressModalProps) {
  const [status, setStatus] = useState<ModalStatus>("connecting");
  const [events, setEvents] = useState<TerraformEvent[]>([]);
  const [resourcesCompleted, setResourcesCompleted] = useState(0);
  const [resourcesTotal, setResourcesTotal] = useState(0);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [showLog, setShowLog] = useState(false);
  const [resources, setResources] = useState<TrackedResource[]>([]);
  const [phase, setPhase] = useState<"storage" | "compute" | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;

    async function streamEvents() {
      const token = getToken();
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
      };
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }

      let response: Response;
      try {
        response = await fetch(`${API_URL}${sseUrl}`, {
          method: "POST",
          headers,
          signal: controller.signal,
        });
      } catch {
        if (!controller.signal.aborted) {
          setStatus("error");
          setErrorMessage("Connection to server lost");
        }
        return;
      }

      if (!response.ok) {
        const body = await response.text().catch(() => "");
        let detail = `Server error (${response.status})`;
        try {
          const parsed = JSON.parse(body);
          if (parsed.detail) detail = parsed.detail;
        } catch {
          // use default detail
        }
        setStatus("error");
        setErrorMessage(detail);
        return;
      }

      setStatus("running");

      const reader = response.body?.getReader();
      if (!reader) {
        setStatus("error");
        setErrorMessage("No response stream available");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const json = line.slice(6);
            let event: TerraformEvent;
            try {
              event = JSON.parse(json);
            } catch {
              continue;
            }

            setEvents((prev) => [...prev, event]);

            if (event.resources_total && event.resources_total > 0) {
              setResourcesTotal(event.resources_total);
            }
            if (
              event.resources_completed !== undefined &&
              event.resources_completed > 0
            ) {
              setResourcesCompleted(event.resources_completed);
            }
            if (event.log_line) {
              setLogLines((prev) => [...prev, event.log_line!]);
            }

            // Track deployment phase for contextual messaging
            if (
              event.message?.toLowerCase().includes("storage") &&
              event.event_type === "progress"
            ) {
              setPhase("storage");
            } else if (
              event.message?.toLowerCase().includes("compute") &&
              event.event_type === "progress"
            ) {
              setPhase("compute");
            }

            // Track individual resources by address
            if (event.resource_address && event.event_type === "resource_complete") {
              const addr = event.resource_address;
              setResources((prev) => {
                const exists = prev.find((r) => r.address === addr);
                if (exists) {
                  return prev.map((r) =>
                    r.address === addr ? { ...r, status: "complete" as const } : r,
                  );
                }
                return [
                  ...prev,
                  { address: addr, label: friendlyLabel(addr), status: "complete" },
                ];
              });
            } else if (
              event.resource_address &&
              event.event_type === "progress" &&
              event.resource_address !== ""
            ) {
              const addr = event.resource_address;
              setResources((prev) => {
                const exists = prev.find((r) => r.address === addr);
                if (!exists) {
                  return [
                    ...prev,
                    {
                      address: addr,
                      label: friendlyLabel(addr),
                      status: "in_progress",
                    },
                  ];
                }
                return prev;
              });
            }

            if (event.event_type === "stack_complete") {
              setStatus("complete");
              return;
            } else if (event.event_type === "apply_complete") {
              setStatus("complete");
              return;
            } else if (event.event_type === "phase_complete") {
              // Mark all tracked resources as complete for this phase
              setResources((prev) =>
                prev.map((r) =>
                  r.status !== "complete" ? { ...r, status: "complete" as const } : r,
                ),
              );
            } else if (
              event.event_type === "apply_error" ||
              event.event_type === "stack_error"
            ) {
              setStatus("error");
              setErrorMessage(event.message);
              return;
            }
          }
        }
        setStatus((prev) => {
          if (prev === "running" || prev === "connecting") {
            setErrorMessage("Stream ended unexpectedly");
            return "error";
          }
          return prev;
        });
      } catch {
        if (!controller.signal.aborted) {
          setStatus("error");
          setErrorMessage("Connection to server lost");
        }
      }
    }

    streamEvents();

    return () => {
      controller.abort();
    };
  }, [sseUrl]);

  // Auto-scroll resource list
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [resources]);

  const progressPct =
    resourcesTotal > 0
      ? Math.min(100, Math.round((resourcesCompleted / resourcesTotal) * 100))
      : 0;

  const phaseLabel =
    phase === "storage"
      ? "Setting up data storage"
      : phase === "compute"
        ? "Setting up compute cluster"
        : "Preparing infrastructure";

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6">
        <h2 className="text-lg font-semibold mb-1">{title}</h2>

        <div data-testid="tf-modal-status" className="mb-4">
          {status === "connecting" && (
            <p className="text-sm text-gray-500">Connecting...</p>
          )}
          {status === "running" && (
            <div>
              <p className="text-sm text-blue-600 flex items-center gap-2">
                <span className="inline-block h-3 w-3 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
                {phaseLabel}
              </p>
              {phase === "compute" && (
                <p className="text-xs text-gray-400 mt-1">
                  Cluster setup typically takes 5 to 15 minutes
                </p>
              )}
            </div>
          )}
          {status === "complete" && (
            <p className="text-sm text-green-600 font-medium">
              All systems ready
            </p>
          )}
          {status === "error" && (
            <p
              data-testid="tf-modal-error"
              className="text-sm text-red-600 font-medium"
            >
              Setup failed: {errorMessage}
            </p>
          )}
        </div>

        {resourcesTotal > 0 && (
          <div data-testid="tf-progress-bar" className="mb-4">
            <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <p className="text-xs text-gray-400 mt-1 text-right">
              {resourcesCompleted} of {resourcesTotal} components
            </p>
          </div>
        )}

        {resources.length > 0 && (
          <ul
            ref={listRef}
            className="space-y-0.5 mb-4 max-h-52 overflow-y-auto"
          >
            {resources.map((r) => (
              <li
                key={r.address}
                className="flex items-center justify-between py-1.5 px-2 rounded text-sm"
              >
                <span
                  className={
                    r.status === "complete"
                      ? "text-gray-700"
                      : r.status === "in_progress"
                        ? "text-gray-900"
                        : "text-gray-400"
                  }
                >
                  {r.label}
                </span>
                <StatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}

        {logLines.length > 0 && (
          <div className="mb-4">
            <button
              onClick={() => setShowLog((v) => !v)}
              className="text-xs text-gray-400 hover:text-gray-600 underline"
            >
              {showLog ? "Hide" : "Show"} technical log
            </button>
            {showLog && (
              <pre className="mt-2 text-xs bg-gray-50 rounded p-2 max-h-32 overflow-y-auto font-mono text-gray-500">
                {logLines.join("\n")}
              </pre>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          {status === "complete" && (
            <button
              data-testid="tf-modal-done-btn"
              onClick={() => {
                onComplete();
                onClose();
              }}
              className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"
            >
              Done
            </button>
          )}
          {status === "error" && (
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
            >
              Close
            </button>
          )}
          {(status === "connecting" || status === "running") && (
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
