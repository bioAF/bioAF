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
  claims: ReportClaim[];
  claim_counts: { total: number; mapped: number; tested: number; label: string };
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
