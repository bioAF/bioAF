// Naming profiles, auto-ingest, pipeline triggers and experiment auto-run.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.
//
// Naming Profiles were redesigned (see local/Naming Profiles/redesign-plan.md).
// The closed enum of field names is gone; profiles now carry an optional
// experiment_template_id and a list of segments shaped by the new
// SegmentDefinition.

export type SegmentFieldType = "string" | "number" | "date";

export type SegmentDateFormat = "YYYYMMDD" | "YYYY-MM-DD" | "YYMMDD";

export type NamingProfileDelimiter = "_" | "-";

export interface SegmentDefinition {
  position: number;
  identifier: string | null;
  field_name: string;
  field_type: SegmentFieldType;
  padding: number | null;
  date_format: SegmentDateFormat | null;
  is_system_chip: boolean;
}

export interface NamingProfile {
  id: number;
  organization_id: number;
  name: string;
  description: string | null;
  delimiter: NamingProfileDelimiter;
  strip_extension: boolean;
  segments: SegmentDefinition[];
  experiment_template_id: number | null;
  status: "active" | "inactive" | "deprecated";
  created_by: number;
  created_at: string;
  updated_at: string;
}

export interface NamingProfileTestResult {
  filename: string;
  parsed: Record<string, string>;
  unrecognized: string[];
  warnings: string[];
}

export interface IngestEvent {
  id: number;
  file_id: number | null;
  source_bucket: string;
  source_path: string;
  naming_profile_id: number | null;
  parsed_project_code: string | null;
  parsed_experiment_code: string | null;
  parsed_sample_id: string | null;
  resolved_project_id: number | null;
  resolved_experiment_id: number | null;
  resolved_sample_id: number | null;
  auto_created_entities: Record<string, number[]>;
  ingest_status: "cataloged" | "unmatched" | "multiple_matches" | "duplicate" | "failed";
  created_at: string;
}

export interface UnclaimedEntity {
  entity_type: "project" | "experiment" | "sample";
  entity_id: number;
  name: string;
  created_at: string;
}

export interface PipelineTrigger {
  id: number;
  pipeline_id: number;
  organization_id: number;
  trigger_mode: "manual" | "event_driven" | "scheduled";
  event_config: Record<string, unknown> | null;
  schedule_config: Record<string, unknown> | null;
  parameter_defaults: Record<string, unknown> | null;
  budget_config: Record<string, unknown>;
  enabled: boolean;
  created_by: number;
  created_at: string;
  updated_at: string;
}

export interface TriggerEvaluation {
  id: number;
  trigger_id: number;
  evaluation_type: string;
  matched_files: number[];
  budget_check_result: Record<string, unknown> | null;
  result: "submitted" | "queued" | "skipped" | "no_files" | "error";
  pipeline_run_id: number | null;
  created_at: string;
}

export interface BudgetStatus {
  monthly_budget: number;
  current_spend: number;
  remaining: number;
  utilization_pct: number;
}

export interface CostEstimate {
  estimated_cost: number;
  confidence_interval_pct: number;
  historical_run_count: number;
  budget_check: Record<string, unknown>;
}

export interface AutoRunConfig {
  id: number;
  experiment_id: number;
  pipeline_key: string;
  parameters: Record<string, unknown> | null;
  reference_genome: string | null;
  alignment_algorithm: string | null;
  delay_minutes: number;
  enabled: boolean;
  configured_by_user_id: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface AutoRunConfigCreate {
  pipeline_key: string;
  parameters?: Record<string, unknown>;
  reference_genome?: string | null;
  alignment_algorithm?: string | null;
  delay_minutes?: number;
}

export interface AutoRunConfigUpdate {
  parameters?: Record<string, unknown>;
  reference_genome?: string | null;
  alignment_algorithm?: string | null;
  delay_minutes?: number;
  enabled?: boolean;
}

export interface PendingAutoRun {
  id: number;
  auto_run_config_id: number;
  experiment_id: number;
  sample_id: number;
  sample_completed_at: string;
  scheduled_at: string;
  status: "waiting" | "launched" | "cancelled";
  pipeline_run_id: number | null;
  cancelled_reason: string | null;
  created_at: string | null;
}
