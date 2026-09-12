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

export interface ClaimConsistency {
  outcome: "agree" | "disagree" | "unresolved" | "not_checkable" | null;
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

export interface CompletionFact {
  key: string;
  label: string;
  value: Tristate;
  value_label: string;
  reason: string | null;
}

export interface ReportSummary {
  version: number;
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
  blockers: { text: string; kind: string | null; basis: string; provisional: boolean }[];
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
}

// Shown beside anything that rests on the paper's prose alone (section 6).
export const PROVISIONAL_NOTE = "from the paper text only; not checked against the attachments";
