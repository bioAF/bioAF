// Compile-time bindings between the pilot transport aliases and the operations consumers call.
//
// Each alias in `src/lib/types/` is derived from one operation's response (for example
// `SessionListResponse` from GET /api/v1/notebooks/sessions). Consumers also call other routes
// that answer the same shape; each is held exactly equal here, so a change to any of those
// operations fails `tsc` after `npm run contracts:generate`. `Equal` is exact (bidirectional
// assignability is not enough: an extra optional field would pass it). `NotNever` guards against
// an alias silently becoming `never` (assignable to everything) if an operation disappears.

import type { paths } from "../contracts/internal-api";
import type {
  ExperimentReference,
  NotebookSession,
  PipelineRunReview,
  PipelineRunReviewCreate,
  PipelineRunReviewListResponse,
  Project,
  ProjectDetailResponse,
  ProjectListResponse,
  ProjectSummary,
  ProvenanceDAG,
  ProvenanceEdge,
  ProvenanceNode,
  SessionListResponse,
  UserSummary,
} from "@/lib/types";

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type Expect<T extends true> = T;
type NotNever<T> = [T] extends [never] ? false : true;

type Json200<P extends keyof paths, M extends "get" | "post"> = paths[P][M] extends {
  responses: { 200: { content: { "application/json": infer B } } };
}
  ? B
  : never;
type Json201<P extends keyof paths> = paths[P]["post"] extends {
  responses: { 201: { content: { "application/json": infer B } } };
}
  ? B
  : never;

export type Bindings = [
  // Nothing bound collapsed to never.
  Expect<NotNever<SessionListResponse>>,
  Expect<NotNever<NotebookSession>>,
  Expect<NotNever<UserSummary>>,
  Expect<NotNever<ProjectSummary>>,
  Expect<NotNever<ExperimentReference>>,
  Expect<NotNever<Project>>,
  Expect<NotNever<ProjectListResponse>>,
  Expect<NotNever<ProjectDetailResponse>>,
  Expect<NotNever<ProvenanceDAG>>,
  Expect<NotNever<ProvenanceNode>>,
  Expect<NotNever<ProvenanceEdge>>,
  Expect<NotNever<PipelineRunReviewListResponse>>,
  Expect<NotNever<PipelineRunReview>>,
  Expect<NotNever<PipelineRunReviewCreate>>,

  // Notebook sessions: the notebooks page uses /api/v1/notebooks; the experiment activity tab
  // lists and launches through /api/notebooks.
  Expect<Equal<Json200<"/api/notebooks/sessions", "get">, SessionListResponse>>,
  Expect<Equal<Json200<"/api/v1/notebooks/sessions", "post">, NotebookSession>>,
  Expect<Equal<Json200<"/api/notebooks/sessions", "post">, NotebookSession>>,
  Expect<Equal<Json200<"/api/v1/notebooks/sessions/{session_id}", "get">, NotebookSession>>,
  Expect<Equal<Json200<"/api/v1/notebooks/sessions/{session_id}/stop", "post">, NotebookSession>>,

  // Nested summaries are the same wherever consumers read them.
  Expect<Equal<NonNullable<PipelineRunReview["reviewer"]>, UserSummary>>,

  // Run reviews: the panel reads the list and the active review and posts a new one.
  Expect<Equal<Json200<"/api/pipeline-runs/{run_id}/review", "get">, PipelineRunReview | null>>,
  Expect<Equal<Json201<"/api/pipeline-runs/{run_id}/reviews">, PipelineRunReview>>,
];
