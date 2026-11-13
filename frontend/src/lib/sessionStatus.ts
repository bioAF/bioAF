// Map a session row to the labels the Notebook + Work Node tables display.
//
// formatSessionStatusLabel folds the backend (status, failure_reason) pair
// into one user-facing string. When a session is in "failed" state we read
// failure_reason from the new taxonomy column populated by the K8s notebook
// adapter (via classify_pod_failure) and the GCE work-node adapter (via
// classify_gce_vm_failure). Anything else falls through to the raw status.
//
// formatLinkedTo collapses the (experiment, project) pair into one "Linked to"
// cell. Only one of the two can be set on a row.

import type { ExperimentReference, ProjectSummary } from "@/lib/types";

// The session APIs link an experiment or project by id and name only.
type LinkSummary = ExperimentReference | ProjectSummary | null | undefined;

const FAILURE_REASON_LABELS: Record<string, string> = {
  resource_exhausted: "Resource Failure",
  image_pull_failed: "Image Pull Failed",
  oom_killed: "Out of Memory",
  quota_exceeded: "Quota Exceeded",
};

export function formatSessionStatusLabel(args: {
  status: string;
  failure_reason: string | null | undefined;
}): string {
  if (args.status === "failed") {
    const reason = args.failure_reason ?? null;
    if (reason && FAILURE_REASON_LABELS[reason]) return FAILURE_REASON_LABELS[reason];
    return "Failed";
  }
  return args.status;
}

export function formatLinkedTo(args: {
  experiment?: LinkSummary;
  project?: LinkSummary;
}): string | null {
  if (args.experiment) return `Experiment: ${args.experiment.name}`;
  if (args.project) return `Project: ${args.project.name}`;
  return null;
}
