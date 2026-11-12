// Compile-time contracts for frontend transport types, checked by CI's `tsc --noEmit`.
//
// Each positive case is a real-shaped payload the backend emits and must type-check.
// Each `@ts-expect-error` is a shape the contract must reject: if a later change starts
// accepting it, the directive itself becomes an error and the gate fails.
// No runtime code here; nothing imports this file.

import type {
  ExperimentReference,
  NotebookSession,
  PipelineRun,
  PipelineRunReview,
  PipelineRunReviewCreate,
  PipelineRunReviewListResponse,
  ProjectDetailResponse,
  ProjectExperimentSummary,
  ProvenanceDAG,
  ProvenanceNode,
  SampleVerdictEntry,
} from "@/lib/types";

// --- experiment link versus project-detail experiment summary ------------------------------

// The session and run APIs emit only id and name for the linked experiment.
export const sessionExperiment: NotebookSession["experiment"] = { id: 7, name: "Liver atlas" };
export const runExperiment: PipelineRun["experiment"] = { id: 7, name: "Liver atlas" };
export const reference: ExperimentReference = { id: 7, name: "Liver atlas" };

// The project detail API emits the richer summary, with a nullable creation time.
export const projectExperiment: ProjectDetailResponse["experiments"][number] = {
  id: 7,
  name: "Liver atlas",
  status: "registered",
  sample_count: 3,
  created_at: null,
};

// @ts-expect-error A bare link is not a project-detail experiment summary.
export const linkAsSummary: ProjectExperimentSummary = { id: 7, name: "Liver atlas" };

// --- provenance graph node -----------------------------------------------------------------

export const graphNode: ProvenanceNode = { id: "experiment-7", type: "experiment", label: "Liver atlas", metadata: {} };
export const graph: ProvenanceDAG = {
  nodes: [graphNode],
  edges: [{ source: "project-1", target: "experiment-7", relationship: "contains" }],
};

// @ts-expect-error A graph node needs its string identity.
export const nodeWithoutId: ProvenanceNode = { type: "experiment", label: "Liver atlas", metadata: {} };

export const chainShapedNode: ProvenanceNode = {
  // @ts-expect-error The retired chain-node shape is not a graph node.
  entity_type: "experiment",
  entity_id: 7,
  label: "Liver atlas",
  timestamp: null,
  children: [],
  metadata: {},
};

// --- run reviews ---------------------------------------------------------------------------

// Shaped like backend/app/api/pipeline_run_reviews.py:_review_response.
const backendReview = {
  id: 3,
  pipeline_run_id: 11,
  reviewer: { id: 2, name: null, email: "reviewer@lab.test" },
  verdict: "approved_with_caveats",
  notes: null,
  sample_verdicts_json: { "41": { verdict: "warning", notes: "low viability" } },
  recommended_exclusions: [41],
  reviewed_at: "2026-11-12T10:00:00Z",
  is_active: true,
  created_at: "2026-11-12T10:00:00Z",
};
export const review: PipelineRunReview = backendReview;
export const reviewWithoutReviewer: PipelineRunReview = { ...backendReview, reviewer: null };
// The list response has no total.
export const reviewList: PipelineRunReviewListResponse = { reviews: [backendReview] };

export const sampleVerdict: SampleVerdictEntry = { verdict: "warning", notes: null };
// @ts-expect-error A sample QC verdict is pass/warning/fail, not a run-review verdict.
export const runVerdictOnSample: SampleVerdictEntry = { verdict: "approved", notes: null };

export const reviewRequest: PipelineRunReviewCreate = {
  verdict: "approved",
  notes: null,
  sample_verdicts: { "41": { verdict: "pass" } },
};
// @ts-expect-error A run-review verdict outside the accepted set is not a valid request.
export const badRunVerdict: PipelineRunReviewCreate = { verdict: "pass" };
