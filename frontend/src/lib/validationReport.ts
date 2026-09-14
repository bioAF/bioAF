/**
 * change_7.3 section 11: the report projection, as the backend's `validation_report_summary`
 * produces it. The page, the JSON export and the markdown export render the same statements from
 * the same evidence; this file is the frontend's half of that contract, and
 * `components/validation/__fixtures__/reportContract.json` (written by the backend's
 * `test_report_contract.py`) is what the component tests render.
 */

export type Tristate = "yes" | "no" | "not_established" | "unknown";

export interface ReportLimitation {
  kind: string;
  label: string;
  resource: string | null;
  leg: string | null;
  detail: string | null;
  observation: string | null;
  // change_7.4 section 1.1: the typed cause and where it happened, shown collapsed under the detail.
  technical_detail?: TechnicalDetail | null;
  // False for a route leg that was not chosen: reported as context, never run.
  governs: boolean;
}

export interface TechnicalDetail {
  [key: string]: string | number | null | undefined;
}

export interface RetrievalFailure {
  source: string;
  message: string;
  artifacts: string[];
  technical_detail: TechnicalDetail;
}

export interface ReportArtifact {
  identity: string | null;
  label: string;
  filename: string | null;
  kind: string;
  references: string[];
  identification: string[];
  identification_label: string;
  retrieval: { status: string; label: string; ledger: string | null; recorded_reason: string | null };
  inspection: { status: string; role: string | null; role_label: string | null; measurements: string[] };
}

export interface ReportCodeSource {
  label: string | null;
  kind: string | null;
  identification: string[];
  identification_label: string;
  retrieval: { status: string; label: string; reason: string | null };
  inspection: { status: string; role_label: string | null };
  execution: { status: string; label: string; outcome: string | null };
}

export interface CapabilityRow {
  key: string;
  label: string;
  value: string | null;
  value_label: string | null;
  detail: string | null;
}

export interface ReportClaim {
  description: string;
  value: number | null;
  unit: string | null;
  population: string | null;
  stage: string | null;
  contrast: string | null;
  cutoff: string | null;
  basis: string;
  provisional: boolean;
  unresolved_reason: string | null;
  mapping: {
    status: string;
    label: string;
    explanation: string | null;
    metric_key: string | null;
    bound_key: string | null;
    confidence: number | null;
    model: string | null;
    reason: string | null;
    decided_by: string;
  };
  // change_7.5 sections 2.2, 2.5 and 2.6. Empty or null on a plan read before experiments existed.
  experiment?: { id: string; assay: string | null } | null;
  checks?: ClaimCheck[];
  selection?: {
    status: "selected" | "unassessed";
    label: string;
    check_label: string | null;
    reason: string | null;
  } | null;
  // change_7.5 sections 3.1, 4.1 and 4.3: the statistical definition in words, the claim checked
  // against the authors' results, and the reanalysis scored for this claim.
  predicate?: string | null;
  consistency?: ClaimConsistency | null;
  result?: {
    tier: string;
    verdict: string | null;
    ground_truth: string | null;
    count: { count: number; status: string; words: string; label: string } | null;
  } | null;
}

// plan_8_2 section 2.1: whether an on-request recovery would change anything, and the last one run.
export interface ReportRecovery {
  available: boolean;
  affected_count: number;
  last: { at: string | null; actor: number | null; reason: string | null; requeued: number | null } | null;
}

export interface ClaimConsistency {
  outcome: "agree" | "disagree" | "unresolved" | "not_checkable" | "pending_re_evaluation" | null;
  label: string | null;
  reason: string | null;
  table: string | null;
  source: string | null;
  rows_tested: number | null;
  rows_passing: number | null;
  rows_missing: number | null;
  count_range: number[] | null;
  candidates: { interpretation: string | null; count: number | null }[];
  assumptions: string[];
  // plan_8_1 section 3.2: the claim's own check record, when the study has them.
  check_state?: string | null;
  check_state_label?: string | null;
  identified_by?: string | null;
  // plan_8_2 section 1.1: the table's binding to the claim's contrast, and a comparison made before
  // bindings, kept for inspection and never current evidence.
  binding?: { status: string | null; reason: string | null; version: number | null; evidence: string[] } | null;
  superseded?: {
    outcome: string | null;
    label: string | null;
    reason: string | null;
    table: string | null;
    rows_tested: number | null;
    rows_passing: number | null;
  } | null;
  // plan_8_2 section 3.2: a count of the authors' published list, and the passage naming the file as the list.
  method?: string | null;
  list?: ClaimList | null;
}

export interface ClaimList {
  evidence: { text: string | null; source: string | null } | null;
  dedup: string | null;
  missing: string | null;
  subgroup: { definition: string | null; field: string | null; unmapped: number | null } | null;
}

export interface ClaimCheck {
  key: string;
  label: string;
  status: "available" | "unavailable" | "unresolved" | null;
  status_label: string | null;
  reason: string | null;
}

export interface ReportResource {
  id: string | null;
  identifier: string | null;
  type: string | null;
  type_label: string;
  archive: string | null;
  role: string | null;
  stated_in: string | null;
  found_by: string[];
  reported_experiment_ids: string[];
  linked_by: string | null;
  retrievable: string | null;
  retrievable_label: string | null;
  analyzable: string | null;
  analyzable_label: string | null;
  limitation: string | null;
  // plan_8_2 section 2.2: support as separate facts, where to open the resource, and how it was named.
  support?: {
    recognized: string;
    metadata_verified: string;
    download_supported: string;
    analysis_supported: string;
    access: string;
  } | null;
  support_labels?: Record<string, string> | null;
  link?: string | null;
  level?: string | null;
  references?: string[];
  split_from?: string | null;
}

export interface ReportExperiment {
  id: string | null;
  assay: string | null;
  workflow: string | null;
  status: string | null;
  reference: {
    part: "assembly" | "annotation";
    stated: string | null;
    resolved: string | null;
    status: string | null;
    status_label: string | null;
    reason: string | null;
    established_from: string | null;
  }[];
}

export interface ReportSelection {
  revision: number | null;
  claim_index: number | null;
  check: string | null;
  check_label: string | null;
  workflow: string | null;
  experiment_id: string | null;
  decided_by: string | null;
  reason: string | null;
  confidence: number | null;
  superseded_revisions: number;
}

/**
 * plan_8: the Validation Scorecard. Every number and every word is the backend's
 * (`validation_scorecard.build_scorecard`); the frontend never computes a metric.
 */
export type FindingStatus = "supported" | "discrepancy" | "inconclusive" | "blocked" | "unresolved" | "not_attempted";
export type ScorecardStatus =
  | "scored"
  | "not_assessed"
  | "not_established"
  | "not_applicable"
  | "unavailable"
  // plan_8_1 section 2.1: the claims are committed and the findings are being established.
  | "pending";

export interface ScorecardItem {
  finding_id: string;
  description: string | null;
  locator: string | null;
  // plan_8_1 section 2.3: null while the importance is not validated (its proposal is `proposed_category`).
  category: "primary" | "supporting" | "technical" | null;
  proposed_category?: string | null;
  category_label: string;
  weight: number | null;
  importance_status?: "validated" | "proposed" | "unknown";
  importance_problem?: string | null;
  rationale: string | null;
  quote: string | null;
  claim_indices: number[];
  status: FindingStatus;
  status_label: string;
  reason: string | null;
  cause: string | null;
  cause_label: string | null;
  assessment_method: string | null;
  method_label: string | null;
  supporting_checks: { kind: string; text: string }[];
  // plan_8_1 stage 4 (version 2 only): how deep the assessment went, what governs it, and what disagreed.
  depth?: ScorecardDepth | null;
  depth_label?: string | null;
  governing?: { method: string | null; evidence: string[] } | null;
  concerns?: { kind: string; text: string }[];
  check_causes?: ScorecardCheckCause[];
  experiment_ids?: string[];
  resource_statements?: string[];
}

export type ScorecardDepth = "independent" | "consistency";

export interface ScorecardCheckCause {
  check: string;
  check_label: string;
  cause: string;
  cause_label: string | null;
  reason: string | null;
}

// plan_8_1 section 4.4: what the paper states about a resource, checked against its record; never scored.
export interface ResourceStatement {
  identifier: string;
  archive: string | null;
  access: string | null;
  experiment_ids: string[];
  outcome: "verified" | "contradicted" | "not_established";
  outcome_label: string;
  checks: { field: string; outcome: string; outcome_label: string; detail: string }[];
}

export interface ScorecardMessage {
  kind: "primary_discrepancy" | "primary_unassessed" | "importance_unestablished" | "concern" | "resource_contradicted";
  text: string;
  findings: string[];
}

export interface CompactScorecard {
  status: ScorecardStatus;
  status_label: string | null;
  score: number | null;
  display_score: number | null;
  score_label: string | null;
  assessed_count: number | null;
  total_count: number | null;
  scope_label: string | null;
  primary_discrepancy_count: number | null;
  primary_unassessed_count: number | null;
  // The primary facts a high score must not hide, worded by the backend.
  indicators: { kind: "primary_discrepancy" | "primary_unassessed" | "importance_unestablished"; text: string }[];
  in_progress: boolean;
  in_progress_label: string | null;
  rubric_version: number;
  inventory_revision: number | null;
  // plan_8_1 section 2.3: a scope whose total holds findings of unestablished importance, and a score
  // withheld while an assessed finding's importance is open.
  provisional?: boolean;
  score_status_label?: string | null;
  // plan_8_1 section 1.3: a card with no score says why and whose limitation that is.
  reason?: string | null;
  cause?: string | null;
  cause_label?: string | null;
  // plan_8_1 section 4.5: under version 2, the depth beside the scope; null under version 1.
  depth_label?: string | null;
  independent_count?: number | null;
  consistency_count?: number | null;
  // plan_8_2 section 1.4: the study's checks by what the queue is doing with them. Checks, never findings.
  activity?: CheckActivity | null;
}

export interface CheckActivity {
  counts: { pending: number; retrying: number; running: number; done: number; unresolved: number; blocked: number };
  completed: number;
  total: number;
  under_way: number;
  label: string | null;
}

export interface ValidationScorecardData extends CompactScorecard {
  version: number;
  title: string;
  rubric_label: string;
  explanation: string;
  provisional_note?: string | null;
  // plan_8_1 section 2.1: the inventory stage failed, and grouping the claims again may establish it.
  inventory_retry?: boolean;
  inventory_status: string | null;
  analysis_selection_revision?: number | null;
  // Set when the outcomes are the ones recorded when the study concluded.
  outcomes_recorded_at?: string | null;
  supported_count: number | null;
  discrepant_count: number | null;
  supported_weight: number | null;
  discrepant_weight: number | null;
  assessed_weight: number | null;
  summary: string | null;
  messages: ScorecardMessage[];
  assessed_items: ScorecardItem[];
  unassessed_items: ScorecardItem[];
  excluded_items: ScorecardItem[];
  unresolved_importance: { finding_id: string; description: string | null; problem: string | null }[];
  resource_statements?: ResourceStatement[];
}

export interface CompletionFact {
  key: string;
  label: string;
  value: Tristate;
  value_label: string;
  reason: string | null;
}

export interface ReportSummary {
  version: number;
  recovery?: ReportRecovery | null;
  attempt: { status: "attempted" | "not_attempted"; executed: string[]; acquired: string[] };
  headline: { key: "reproduction_not_attempted" | "could_not_reproduce" | "verdict" | "in_progress"; label: string | null };
  summary: string[];
  facts: Record<string, unknown>;
  limitations: ReportLimitation[];
  retrieval_failures: RetrievalFailure[];
  artifacts: ReportArtifact[];
  figures: number;
  index_pages: number;
  code_sources: ReportCodeSource[];
  capability_rows: CapabilityRow[];
  // change_7.5 stage 2. Absent from a report written before it.
  resources?: ReportResource[];
  experiments?: ReportExperiment[];
  selection?: ReportSelection | null;
  selection_history?: { revision: number | null; artifacts: string[]; invalidated_by: string[]; at: string | null; label: string }[];
  claims: ReportClaim[];
  claim_counts: { total: number; mapped: number; tested: number; label: string };
  // plan_8_1 section 1.4: on a failed read, a withheld blocker carries the sentence it replaced.
  blockers: { text: string; kind: string | null; basis: string; provisional: boolean; withheld?: string }[];
  // plan_8_1 section 1.4. Null when the read did not fail; absent from a report written before it.
  read_failure?: {
    cause: string;
    legacy: boolean;
    classification_from_failed_read: boolean;
    classification_note: string | null;
  } | null;
  contrasts: {
    name: string | null;
    // change_7.5 section 1.2: the stated cutoff in words ("P < 0.01"), never the legacy pair.
    cutoff: string | null;
    basis: string;
    provisional: boolean;
    // change_7.4 section 1.4: only the selected contrast is validated and executed.
    status: "selected" | "unassessed";
  }[];
  reconciliation: { status: string | null; reason: string | null; basis: string | null; label: string | null };
  consistency: { checked: boolean; pairs: string[]; label: string; unresolved: { statement: string | null; outcome: string | null }[] };
  checks: { key: string; label: string; verdict: string | null; detail: string | null; basis: string; provisional: boolean }[];
  completion_facts: CompletionFact[];
  checks_completed: string[];
  checks_not_completed: string[];
  comparisons: { performed: boolean; label: string | null; reason: string | null };
  resume: { label: string; requirements: string[] };
  issue_count: number;
  // plan_8. Absent from a report projected before the scorecard existed.
  scorecard?: ValidationScorecardData | null;
}

// Shown beside anything that rests on the paper's prose alone (section 6).
export const PROVISIONAL_NOTE = "from the paper text only; not checked against the attachments";
