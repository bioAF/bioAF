/**
 * Resolves the in-app destination for a notification.
 *
 * Every notification points at an associated item. The backend persists the
 * entity reference (`entity_type` / `entity_id`, plus context like
 * `experiment_id`, `pipeline_run_id`, `file_id`) in `metadata_json`; this maps
 * that reference to the page that shows the item. A few event types override the
 * entity rule (for example a completed run goes to its QC dashboard, not the raw
 * run page). Returns `null` when there is no good destination, in which case the
 * notification is rendered as plain, non-clickable text.
 */

export interface NotificationLink {
  event_type: string;
  metadata_json?: Record<string, unknown> | null;
}

// Event types whose "associated item" is the QC dashboard for the run, i.e. the
// "results are ready" moment, rather than the run's own detail page.
const RESULTS_READY_EVENTS = new Set([
  "pipeline.completed",
  "qc.results_ready",
  "results.published",
]);

// Entity types that map to a single fixed page (no per-record detail route).
const SECTION_FOR_ENTITY: Record<string, string> = {
  component: "/infrastructure/components",
  terraform_run: "/infrastructure/components",
  backup: "/infrastructure/backup",
  work_node: "/workbench/work-nodes",
  notebook_session: "/notebooks",
  literature_review_run: "/lab-knowledge/literature",
};

function asInt(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^\d+$/.test(value)) return Number(value);
  return null;
}

export function notificationHref(n: NotificationLink): string | null {
  const md = n.metadata_json ?? {};
  const entityType = typeof md.entity_type === "string" ? md.entity_type : null;
  const entityId = asInt(md.entity_id);
  const experimentId = asInt(md.experiment_id);
  const pipelineRunId = asInt(md.pipeline_run_id);
  const fileId = asInt(md.file_id);

  // "Results ready": go straight to the QC dashboard for the run.
  if (RESULTS_READY_EVENTS.has(n.event_type)) {
    const runId = pipelineRunId ?? (entityType === "pipeline_run" ? entityId : null);
    if (runId != null) return `/results/qc-dashboards?run=${runId}`;
  }

  switch (entityType) {
    case "pipeline_run":
      return entityId != null ? `/pipelines/runs/${entityId}` : null;
    case "pipeline_run_review":
      return pipelineRunId != null ? `/pipelines/runs/${pipelineRunId}` : null;
    case "experiment":
      return entityId != null ? `/experiments/${entityId}` : null;
    case "project":
      return entityId != null ? `/projects/${entityId}` : null;
    case "sample":
      return experimentId != null ? `/experiments/${experimentId}?tab=samples` : null;
    case "file":
      // A file tied to an experiment opens that experiment's Files tab; a
      // standalone file opens the Data & Files page focused on it.
      if (experimentId != null) return `/experiments/${experimentId}?tab=files`;
      return entityId != null ? `/data/files?file=${entityId}` : null;
    case "ingest_event":
      return fileId != null ? `/data/files?file=${fileId}` : null;
    case "reference_dataset":
      return entityId != null ? `/data/references/${entityId}` : null;
    default:
      return entityType ? SECTION_FOR_ENTITY[entityType] ?? null : null;
  }
}
