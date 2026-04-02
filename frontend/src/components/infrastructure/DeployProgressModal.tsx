"use client";

import { useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Friendly names for Terraform resource types
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
  "google_project_iam_member.gke_default_node_sa":
    "Node service account permissions",
  "google_service_account.notebook_runner":
    "Notebook service account",
  "google_project_iam_member.notebook_runner_storage":
    "Notebook storage permissions",
  "google_service_account_iam_member.notebook_runner_workload_identity":
    "Notebook identity binding",
};

const PATIENCE_MESSAGES = [
  "Doing the science...",
  "Arguing with reviewer #2...",
  "Negotiating with the sequencer...",
  "Asking the compute cluster nicely...",
  "Wrangling sample metadata...",
  "Spinning up nodes (they're shy)...",
  "Convincing Kubernetes this is a good idea...",
  "Almost there. Probably.",
  "Aligning reads...",
  "Realigning reads...",
  "Realigning the realignment...",
  "Counting reads...",
  "Counting them again to be sure...",
  "Finding that one weird SNP...",
  "Ignoring mitochondiral reads...",
  "Blaming the reference genome...",
  "Indexing the reference genome...",
  "Re-indexing the reference genome...",
  "Scaffolding genomes...",
  "Looking for structural variants...",
  "Pretending we understand structural variants...",
  "Explaining TPM to the system again...",
  "Explaining FPKM to the system again...",
  "Pretending TPM vs FPKM matters less than it does...",
  "Arguing about cluster labels",
  "Growing cells...",
  "Waiting for cells to grow...",
  "Waiting longer for cells to grow...",
  "Checking the incubator...",
  "Feeding the cells...",
  "Sacrificing a pipette tip...",
  "Running another gel...",
  "Waiting for the centrifuge...",
  "Balancing the centrifuge again just in case...",
  "Submitting job to the queue...",
  "Waiting in the queue...",
  "Still waiting in the queue...",
  "Looking for the missing sample...",
  "Looking harder for the missling sample...",
  "Checking the -80...",
  "Defrosting the -80...",
  "Questioning the methods section...",
  "Questioning everything...",
  "Untangling phylogenetic trees...",
  "Teaching BLAST new tricks...",
  "Negotiating with NCBI...",
  "Bribing the alignment algorithm...",
  "Searching for conserved domains...",
  "Looking for signal peptides...",
  "Convincing proteins to fold correctly...",
  "Consulting the ribosome...",
  "SLURM says maybe...",
  "Explaining TPM to the PI...",
  "Downloading another 200GB reference genome...",
  "Recreating the environment (again)...",
];

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DeployProgressModalProps {
  phase: string | null;
  status: string | null;
  resourcesCompleted: number;
  resourcesTotal: number;
  completedResources: string[];
  errorMessage: string | null;
  onDismiss: () => void;
  onAbort: () => void;
  onDone: () => void;
}

type ResourceStatus = "pending" | "complete";

interface TrackedResource {
  address: string;
  label: string;
  status: ResourceStatus;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function friendlyLabel(address: string): string {
  if (FRIENDLY_NAMES[address]) return FRIENDLY_NAMES[address];
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
  return (
    <span className="text-xs text-gray-400 uppercase tracking-wide">
      Queued
    </span>
  );
}

function phaseTitle(phase: string | null, status: string | null): string {
  if (status === null || status === "planning" || status === "awaiting_confirmation")
    return "Preparing deployment";
  if (phase === "storage") return "Deploying storage infrastructure";
  if (phase === "compute") return "Deploying compute infrastructure";
  return "Preparing deployment";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DeployProgressModal({
  phase,
  status,
  resourcesCompleted,
  resourcesTotal,
  completedResources,
  errorMessage,
  onDismiss,
  onAbort,
  onDone,
}: DeployProgressModalProps) {
  const listRef = useRef<HTMLUListElement | null>(null);
  const [patienceIndex, setPatienceIndex] = useState(0);

  const isRunning = status === null || status === "planning" || status === "applying" || status === "awaiting_confirmation";
  const isComplete = status === "completed";
  const isError = status === "failed";
  const showTimingWarning = phase === "compute";

  // Rotate patience messages every 10 seconds while running
  useEffect(() => {
    if (!isRunning) return;
    const interval = setInterval(() => {
      setPatienceIndex((prev) => (prev + 1) % PATIENCE_MESSAGES.length);
    }, 10000);
    return () => clearInterval(interval);
  }, [isRunning]);

  // Build resource list from completedResources
  const resources: TrackedResource[] = completedResources.map((addr) => ({
    address: addr,
    label: friendlyLabel(addr),
    status: "complete" as const,
  }));

  // Auto-scroll resource list
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [resources.length]);

  const progressPct =
    resourcesTotal > 0
      ? Math.min(100, Math.round((resourcesCompleted / resourcesTotal) * 100))
      : 0;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6">
        <h2 className="text-lg font-semibold mb-1">
          {isComplete ? "Deployment complete" : isError ? "Deployment failed" : phaseTitle(phase, status)}
        </h2>

        <div data-testid="deploy-modal-status" className="mb-4">
          {isRunning && (
            <div>
              <p className="text-sm text-blue-600 flex items-center gap-2">
                <span className="inline-block h-3 w-3 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
                {status === null || status === "planning" || status === "awaiting_confirmation"
                  ? "Planning resources..."
                  : "Applying changes..."}
              </p>
              <div className="mt-2">
                {showTimingWarning && (
                  <p className="text-xs text-gray-400">
                    This can take 10-30 minutes based on Google Cloud traffic.
                  </p>
                )}
                <p className="text-xs text-gray-300 mt-1">
                  {PATIENCE_MESSAGES[patienceIndex]}
                </p>
              </div>
            </div>
          )}
          {isComplete && (
            <p className="text-sm text-green-600 font-medium">
              All systems ready
            </p>
          )}
          {isError && (
            <p
              data-testid="deploy-modal-error"
              className="text-sm text-red-600 font-medium"
            >
              Setup failed: {errorMessage}
            </p>
          )}
        </div>

        {resourcesTotal > 0 && (
          <div data-testid="deploy-progress-bar" className="mb-4">
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
                <span className="text-gray-700">{r.label}</span>
                <StatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}

        <div className="flex justify-end gap-2">
          {isComplete && (
            <button
              data-testid="deploy-modal-done-btn"
              onClick={onDone}
              className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"
            >
              Done
            </button>
          )}
          {isError && (
            <button
              onClick={onDismiss}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
            >
              Close
            </button>
          )}
          {isRunning && (
            <>
              <button
                onClick={onAbort}
                className="px-4 py-2 bg-red-50 text-red-600 rounded-lg text-sm font-medium hover:bg-red-100"
              >
                Abort Deployment
              </button>
              <button
                onClick={onDismiss}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
              >
                Minimize
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
