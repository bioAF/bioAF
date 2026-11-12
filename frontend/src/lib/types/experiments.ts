// Projects, experiments, samples, batches, vocabularies and sample-sheet import.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { UserSummary } from "./identity";

export type ExperimentStatus =
  | "registered"
  | "library_prep"
  | "sequencing"
  | "fastq_uploaded"
  | "processing"
  | "pipeline_complete"
  | "reviewed"
  | "analysis"
  | "complete";

export type QCStatus = "pass" | "warning" | "fail";

// Optional, first-class assay (controlled system vocabulary; mirrors the backend
// SAMPLE_ASSAYS in app/models/sample.py). When set it is the authoritative signal for
// pipeline recommendation; when null, the assay is inferred from the free-text fields.
export type AssayValue = "bulk_rna" | "scrna" | "other";

export const SAMPLE_ASSAY_OPTIONS: { value: AssayValue; label: string }[] = [
  { value: "bulk_rna", label: "Bulk RNA-seq" },
  { value: "scrna", label: "Single-cell RNA-seq" },
  { value: "other", label: "Other" },
];

export type SampleStatus =
  | "registered"
  | "library_prepped"
  | "sequenced"
  | "fastq_uploaded"
  | "pipeline_complete"
  | "analysis_complete";

export interface ProjectSummary {
  id: number;
  name: string;
}

export interface Project {
  id: number;
  name: string;
  code: string | null;
  external_id: string | null;
  description: string | null;
  hypothesis: string | null;
  status: string | null;
  owner_user_id: number | null;
  owner_name: string | null;
  sample_count: number;
  experiment_count: number;
  pipeline_run_count: number;
  snapshot_count: number;
  created_at: string;
}

export interface ProjectSampleResponse {
  sample_id: number;
  external_id: string | null;
  organism: string | null;
  tissue_type: string | null;
  qc_status: QCStatus | null;
  added_by: string | null;
  added_at: string | null;
  notes: string | null;
}

export interface ProjectSampleGroup {
  experiment_id: number;
  experiment_name: string;
  samples: ProjectSampleResponse[];
}

export interface PipelineRunSummary {
  id: number;
  pipeline_name: string;
  pipeline_version: string | null;
  status: string;
  created_at: string | null;
}

/** An experiment as the project detail API lists it (backend `schemas/project.py:ExperimentSummary`). */
export interface ProjectExperimentSummary {
  id: number;
  name: string;
  status: string;
  sample_count: number;
  created_at: string | null;
}

export interface ProjectDetailResponse extends Project {
  samples: ProjectSampleGroup[];
  experiments: ProjectExperimentSummary[];
  pipeline_runs: PipelineRunSummary[];
}

export interface ProjectListResponse {
  projects: Project[];
  total: number;
}

export interface Experiment {
  id: number;
  name: string;
  code: string | null;
  external_id: string | null;
  project: ProjectSummary | null;
  template_id: number | null;
  template_name: string | null;
  hypothesis: string | null;
  description: string | null;
  status: ExperimentStatus;
  start_date: string | null;
  expected_sample_count: number | null;
  owner: UserSummary | null;
  sample_count: number;
  batch_count: number;
  design_type: string | null;
  naming_profile_id: number | null;
  template_naming_profile_id: number | null;
  effective_naming_profile_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface ExperimentUpdateRequest {
  name?: string | null;
  hypothesis?: string | null;
  description?: string | null;
  start_date?: string | null;
  expected_sample_count?: number | null;
  design_type?: string | null;
  field_defaults?: FieldDefaultValue[];
  custom_fields?: CustomFieldValue[];
  naming_profile_id?: number | null;
}

export interface ExperimentListResponse {
  experiments: Experiment[];
  total: number;
  page: number;
  page_size: number;
}

export interface CustomFieldValue {
  field_name: string;
  field_value: string;
  field_type: string;
  is_required?: boolean;
}

export interface CustomFieldResponse {
  id: number;
  field_name: string;
  field_value: string | null;
  field_type: string;
  is_required: boolean;
}

export interface SampleBrief {
  id: number;
  external_id: string | null;
  organism: string | null;
  tissue_type: string | null;
  molecule_type: string | null;
  library_prep_method: string | null;
  library_layout: string | null;
  chemistry_version: string | null;
  qc_status: QCStatus | null;
  status: SampleStatus;
  created_at: string;
}

export interface SampleBatchBrief {
  id: number;
  name: string;
  sample_count: number;
  created_at: string | null;
}

export interface FieldDefaultValue {
  field_name: string;
  default_value: string | null;
  is_required: boolean | null;
}

export interface FieldDefaultResponse {
  id: number;
  field_name: string;
  default_value: string | null;
  is_required: boolean | null;
}

export interface ExperimentDetail extends Experiment {
  samples: SampleBrief[];
  sample_batches: SampleBatchBrief[];
  custom_fields: CustomFieldResponse[];
  field_defaults: FieldDefaultResponse[];
  audit_trail_count: number;
}

export interface SampleBatchSummary {
  id: number;
  name: string;
}

export interface SampleCustomFieldValue {
  field_name: string;
  field_value: string;
}

export interface SampleCustomFieldResponse {
  id: number;
  field_name: string;
  field_value: string | null;
}

export interface Sample {
  id: number;
  external_id: string | null;
  /** The source label migration 080 replaced with external_id (the experiment held it twice); else null. */
  relabeled_from?: string | null;
  organism: string | null;
  tissue_type: string | null;
  donor_source: string | null;
  treatment_condition: string | null;
  chemistry_version: string | null;
  sample_batch: SampleBatchSummary | null;
  sequencing_batch: { id: number; code: string } | null;
  sequencing_batch_position: number | null;
  viability_pct: number | null;
  cell_count: number | null;
  prep_notes: string | null;
  molecule_type: string | null;
  library_prep_method: string | null;
  library_layout: string | null;
  assay: string | null;
  qc_status: QCStatus | null;
  qc_notes: string | null;
  file_count: number;
  status: SampleStatus;
  created_at: string;
  updated_at: string;
  custom_fields: SampleCustomFieldResponse[];
}

export interface SampleBatch {
  id: number;
  name: string;
  prep_date: string | null;
  operator: UserSummary | null;
  notes: string | null;
  sample_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export type SequencingBatchStatus = "pending" | "ingesting" | "complete" | "partial_complete" | "failed";

export interface SequencingBatch {
  id: number;
  organization_id: number;
  name: string;
  code: string;
  status: SequencingBatchStatus;
  instrument_model: string | null;
  instrument_platform: string | null;
  quality_score_encoding: string | null;
  sequencer_run_id: string | null;
  manifest_received_at: string | null;
  expected_file_count: number | null;
  ingested_file_count: number;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ManifestEntry {
  id: number;
  expected_filename: string;
  expected_md5: string;
  resolved_sample_id: number | null;
  resolved_experiment_id: number | null;
  resolved_project_id: number | null;
  file_id: number | null;
  status: "pending" | "verified" | "checksum_mismatch" | "missing" | "failed";
  last_check_at: string | null;
  retry_count: number;
  error_message: string | null;
  created_at: string | null;
}

export interface SequencingBatchDetail extends SequencingBatch {
  manifest_entries: ManifestEntry[];
}

export interface ExperimentTemplate {
  id: number;
  name: string;
  description: string | null;
  required_fields_json: Record<string, unknown> | null;
  custom_fields_schema_json: Record<string, unknown> | null;
  naming_profile_id: number | null;
  created_by: UserSummary | null;
  created_at: string;
}

export interface ExperimentCreateRequest {
  name: string;
  project_id?: number | null;
  template_id?: number | null;
  hypothesis?: string | null;
  description?: string | null;
  start_date?: string | null;
  expected_sample_count?: number | null;
  custom_fields?: CustomFieldValue[];
  field_defaults?: FieldDefaultValue[];
  design_type?: string | null;
  column_aliases?: Record<string, string>;
  naming_profile_id?: number | null;
}

export interface SampleCreateRequest {
  external_id?: string | null;
  organism?: string | null;
  tissue_type?: string | null;
  donor_source?: string | null;
  treatment_condition?: string | null;
  chemistry_version?: string | null;
  sample_batch_code?: string | null;
  sequencing_batch_code?: string | null;
  viability_pct?: number | null;
  cell_count?: number | null;
  prep_notes?: string | null;
  molecule_type?: string | null;
  library_prep_method?: string | null;
  library_layout?: string | null;
  assay?: string | null;
  qc_status?: string | null;
  qc_notes?: string | null;
  custom_fields?: SampleCustomFieldValue[];
}

export interface SampleUpdateRequest {
  external_id?: string | null;
  organism?: string | null;
  tissue_type?: string | null;
  donor_source?: string | null;
  treatment_condition?: string | null;
  chemistry_version?: string | null;
  sample_batch_code?: string | null;
  sequencing_batch_code?: string | null;
  viability_pct?: number | null;
  cell_count?: number | null;
  prep_notes?: string | null;
  molecule_type?: string | null;
  library_prep_method?: string | null;
  library_layout?: string | null;
  assay?: string | null;
  custom_fields?: SampleCustomFieldValue[];
}

export interface SampleBulkUpdateRequest {
  sample_ids: number[];
  update: SampleUpdateRequest;
}

export interface SampleBatchCreateRequest {
  name: string;
  prep_date?: string | null;
  operator_user_id?: number | null;
  notes?: string | null;
}

export interface TemplateCreateRequest {
  name: string;
  description?: string | null;
  required_fields_json?: Record<string, unknown> | null;
  custom_fields_schema_json?: Record<string, unknown> | null;
  naming_profile_id?: number | null;
}

/** A link to an experiment: only its id and name, as session and run responses emit it. */
export interface ExperimentReference {
  id: number;
  name: string;
}

export interface ControlledVocabularyValue {
  id: number;
  value: string;
  display_label: string | null;
  display_order: number;
  is_default: boolean;
  is_active: boolean;
}

export interface ControlledVocabularyResponse {
  field_name: string;
  values: ControlledVocabularyValue[];
}

export interface ControlledVocabularyFieldsResponse {
  fields: string[];
}

export interface VocabularyValue {
  id: number;
  value: string;
  display_label: string | null;
  display_order: number;
  is_default: boolean;
  is_active: boolean;
}

export interface VocabularyResponse {
  field_name: string;
  values: VocabularyValue[];
}

export interface SheetRecognizedColumn {
  header: string;
  mapped_to: string;
  defaultable: boolean;
}

export interface SheetPreviewResponse {
  spreadsheet_id: string;
  sheet_name: string;
  columns: string[];
  recognized_columns: SheetRecognizedColumn[];
  unknown_columns: string[];
}

export interface ReaderSAStatus {
  exists: boolean;
  email: string | null;
}

export interface ReaderSACreateResponse {
  email: string;
  message: string;
  warning: string | null;
}
