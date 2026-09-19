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
  // plan_8_2 section 3.1: where the predicate's cutoffs came from; an inherited one names its methods sentence.
  cutoff_source?: { kind: string; quote?: string | null } | null;
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
  restate?: { from: string; to: string } | null;
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
  // plan_8_2 section 3.1: the reading the comparison applied, and what each column of an unread table could be.
  interpretation?: {
    source: string | null;
    version: number | null;
    columns: Record<string, string | null>;
    effect_scale: string | null;
    evidence: string[];
  } | null;
  candidate_roles?: Partial<Record<"id" | "lfc" | "pvalue" | "padj", number[]>> | null;
  columns_count?: number | null;
  // plan_8_4 defect 1: which reading of a documented refinement's cutoff is open, so the page can
  // offer the control that settles it. Null where the comparison refines no published list.
  filter_semantics?: FilterSemantics | null;
}

export interface FilterSemantics {
  unresolved: boolean;
  reason: string | null;
  statement: string | null;
  magnitude: boolean | null;
  resolved_by: string | null;
  note?: string | null;
  confirmed_by?: string | null;
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
  // plan_8_2 section 4.2: the shared reason this finding's reason is stated in, once.
  shared_reason?: string | null;
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
  // plan_8_4 section 7: the v3 evidence score, cut from the same projection the report renders.
  // Absent from a study listed before rubric v3 existed.
  evidence_score?: EvidenceScore | null;
}

export interface CheckActivity {
  // plan_8_4 defect 4: `done` is what the queue finished; `concluded` is what a check settled. A
  // comparison that ran and could not resolve its claim is in both `done` and
  // `finished_without_conclusion`, and never in `concluded`.
  counts: {
    pending: number;
    retrying: number;
    running: number;
    done: number;
    unresolved: number;
    blocked: number;
    concluded: number;
    finished_without_conclusion: number;
    could_not_conclude: number;
  };
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
  // plan_8_2 section 4.2: why a blank score is blank, and what the card counts, each in its own unit.
  score_note?: string | null;
  units?: { key: string; count: number; label: string }[];
}

export interface CompletionFact {
  key: string;
  label: string;
  value: Tristate;
  value_label: string;
  reason: string | null;
}

// plan_8_2 section 4.2 (approved 2026-09-14): the four sections' summaries and counts, from the projection.
export interface ReportCount {
  label: string;
  tone: "ok" | "bad" | "warn" | null;
}

export interface CompactResource {
  identifier: string | null;
  type: string | null;
  archive: string | null;
  link: string | null;
  limitation: string | null;
}

export interface ReportSections {
  findings: {
    summary: string;
    counts: ReportCount[];
    groups: { key: string; label: string; finding_ids: string[] }[];
    shared_reasons: { id: string; text: string; cause_label: string | null; finding_ids: string[] }[];
    open: string[];
    ungrouped_claims: number[];
  };
  data: {
    summary: string;
    counts: ReportCount[];
    unsupported: { resources: CompactResource[]; note: string };
    sample_records: CompactResource[];
  };
  checks: {
    summary: string;
    counts: ReportCount[];
    rows: { key: string; label: string; claims: number; available: number; unresolved: number; unavailable: number }[];
  };
  diagnostics: {
    summary: string;
    counts: ReportCount[];
    checks: {
      check_id: string | null;
      kind: string | null;
      state: string | null;
      activity: string | null;
      revision: number | null;
      retry_count: number;
      terminal_reason: string | null;
      outcome_revision: number | null;
    }[];
  };
}

export interface ReportApplicability {
  status: "applicable" | "partial" | "not_applicable" | "undetermined";
  statement: string | null;
  limitation: string | null;
  experiments: {
    id: string | null;
    assay: string | null;
    workflow: string | null;
    claims: number;
    eligible_claims: number;
    supported: boolean;
    // plan_8_3 section 1.3: what the experiment is supported BY, and why. A workflow name alone is
    // not support, and a check a failed model decision left open is bioAF's failure, never support.
    support?:
      | "supported"
      | "awaiting_input"
      | "unresolved_interpretation"
      | "failed_decision"
      | "unsupported"
      | null;
    support_reason?: string | null;
  }[];
  eligible_claims: number;
}

/**
 * plan_8_3 stage 5: the control for a mapping held because the paper's biological units are not
 * established. `unresolved` are the columns holding it now; `columns` are the ones a confirmation may
 * speak about at all. Absent from a report written before it.
 */
export interface ReportUnitConfirmation {
  matrix: string | null;
  columns: string[];
  unresolved: string[];
  recorded: {
    units: Record<string, string>;
    note: string | null;
    confirmed_by: string | null;
    at: string | null;
    superseded_count: number;
  } | null;
}

export interface ReportSummary {
  version: number;
  recovery?: ReportRecovery | null;
  // plan_8_3 stage 5. Null where nothing is held on a unit identity and none was recorded.
  unit_confirmation?: ReportUnitConfirmation | null;
  // plan_8_2 section 4.1: whether bioAF's current methods apply, per experiment.
  applicability?: ReportApplicability | null;
  // plan_8_2 section 4.2: the four sections. Absent from a report written before them.
  sections?: ReportSections | null;
  attempt: { status: "attempted" | "not_attempted"; executed: string[]; acquired: string[] };
  headline: {
    key: "reproduction_not_attempted" | "could_not_reproduce" | "verdict" | "in_progress" | "not_applicable";
    label: string | null;
  };
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
  // plan_8_3 (reporting): `checked` counts the claims checked against the authors' published
  // results, beside `tested` by reproduction. Absent from a report written before it.
  claim_counts: { total: number; mapped: number; tested: number; checked?: number; label: string };
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
  // plan_8_4. Absent from a report projected before rubric v3 existed.
  evidence_score?: EvidenceScore | null;
}

// Shown beside anything that rests on the paper's prose alone (section 6).
export const PROVISIONAL_NOTE = "from the paper text only; not checked against the attachments";

/**
 * plan_8_4: rubric v3's evidence score. Separate from the v2 scorecard above and never read as it:
 * a v2 score of 100 is not a v3 score of 100. Every number is computed by the backend.
 */
export interface EvidenceScoreSection {
  section: string;
  title: string;
  verified: number;
  failed: number;
  undetermined: number;
  maximum: number;
  established: string[];
  outstanding: string | null;
  unsupported_count: number;
}

export interface EvidenceScoreLimit {
  leaf: string;
  criterion: string;
  section: string;
  points: number;
  reason: string;
}

export interface EvidenceScoreConcern {
  leaf: string;
  criterion: string;
  section: string;
  points: number;
  rationale: string | null;
  impact: string | null;
}

export interface EvidenceScore {
  rubric_version: number;
  rubric_label: string;
  status: string;
  score: number;
  failed: number;
  undetermined: number;
  assessed_points: number;
  display: { verified: string; failed: string; undetermined: string; total: string };
  exact: { verified: string; failed: string; undetermined: string };
  parts: { key: string; label: string; points: string }[];
  headline: string;
  counts_label: string;
  score_note: string | null;
  explanation: string;
  scope: { assessed: number; total: number; label: string };
  sections: EvidenceScoreSection[];
  profile: {
    revision: number | null;
    exclusions: { criterion: string | null; section: string | null; rationale: string; source: string | null }[];
    documentary_ceiling: number;
    with_author_results_ceiling: number;
  };
  capability_limits: EvidenceScoreLimit[];
  reproduction: { attempted: boolean; label: string; reason: string | null };
  concerns: EvidenceScoreConcern[];
}
