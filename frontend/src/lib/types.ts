export interface User {
  id: number;
  email: string;
  name: string | null;
  role_id: number;
  role_name: string;
  status: "active" | "invited" | "deactivated";
  organization_id: number;
  last_login: string | null;
  session_credentials_configured: boolean;
  created_at: string;
  updated_at: string;
}

export interface PermissionEntry {
  resource: string;
  action: string;
}

export interface Role {
  id: number;
  name: string;
  description: string | null;
  organization_id: number;
  is_system: boolean;
  permissions: PermissionEntry[];
  created_at: string;
}

export interface RoleListResponse {
  roles: Role[];
  total: number;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface BootstrapStatus {
  setup_complete: boolean;
  smtp_configured: boolean;
  has_setup_code: boolean;
  has_admin: boolean;
}

export interface ComponentState {
  key: string;
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  status: string;
  config: Record<string, unknown>;
  dependencies: string[];
  estimated_monthly_cost: string;
  updated_at: string | null;
}

export interface TerraformRun {
  id: number;
  triggered_by_user_id: number;
  action: string;
  component_key: string | null;
  plan_summary: {
    add: Array<{ type: string; name: string; address: string }>;
    change: Array<{ type: string; name: string; address: string }>;
    destroy: Array<{ type: string; name: string; address: string }>;
    add_count: number;
    change_count: number;
    destroy_count: number;
  } | null;
  status: string;
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
}

export interface HealthStatus {
  status: "healthy" | "degraded" | "unhealthy";
  services: Record<string, { status: string; error?: string }>;
}

export interface NavItem {
  label: string;
  href: string;
  icon: string;
  active: boolean;
  phase?: string;
  adminOnly?: boolean;
}

// Phase 2 — Experiment Tracking

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

export type SampleStatus =
  | "registered"
  | "library_prepped"
  | "sequenced"
  | "fastq_uploaded"
  | "pipeline_complete"
  | "analysis_complete";

export interface UserSummary {
  id: number;
  name: string | null;
  email: string;
}

export interface ProjectSummary {
  id: number;
  name: string;
}

export interface Project {
  id: number;
  name: string;
  code: string | null;
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
  sample_id_unique: string | null;
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

export interface ExperimentSummary {
  id: number;
  name: string;
  status: string;
  sample_count: number;
  created_at: string | null;
}

export interface ProjectDetailResponse extends Project {
  samples: ProjectSampleGroup[];
  experiments: ExperimentSummary[];
  pipeline_runs: PipelineRunSummary[];
}

export interface ProjectListResponse {
  projects: Project[];
  total: number;
}

export interface ProvenanceNode {
  id: string;
  type: "experiment" | "sample" | "pipeline_run" | "snapshot" | "reference" | "file" | "project";
  label: string;
  metadata: Record<string, unknown>;
}

export interface ProvenanceEdge {
  source: string;
  target: string;
  relationship: string;
}

export interface ProvenanceDAG {
  nodes: ProvenanceNode[];
  edges: ProvenanceEdge[];
}

export interface Experiment {
  id: number;
  name: string;
  code: string | null;
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
  sample_id_unique: string | null;
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
  created_at: string;
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
  sample_id_unique: string | null;
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
  created_at: string;
  updated_at: string;
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
  created_at: string;
  updated_at: string;
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
  created_at: string;
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
  created_by: UserSummary | null;
  created_at: string;
}

export interface AuditLogEntry {
  id: number;
  timestamp: string;
  user: UserSummary | null;
  entity_type: string;
  entity_id: number;
  action: string;
  details: Record<string, unknown> | null;
  previous_value: Record<string, unknown> | null;
}

export interface AuditLogResponse {
  entries: AuditLogEntry[];
  total: number;
  page: number;
  page_size: number;
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
}

export interface SampleCreateRequest {
  sample_id_unique?: string | null;
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
  qc_status?: string | null;
  qc_notes?: string | null;
  custom_fields?: SampleCustomFieldValue[];
}

export interface SampleUpdateRequest {
  sample_id_unique?: string | null;
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
}

// Phase 3 — Compute + Notebooks

export type SlurmJobStatus = "pending" | "running" | "completed" | "failed" | "cancelled" | "timeout";
export type SessionStatus = "pending" | "starting" | "running" | "idle" | "stopping" | "stopped" | "failed";
export type SessionType = "jupyter" | "rstudio";
export type ResourceProfile = "small" | "medium" | "large";

export interface ExperimentSummary {
  id: number;
  name: string;
}

export const RESOURCE_PROFILES: Record<ResourceProfile, { cpu: number; memory: number }> = {
  small: { cpu: 2, memory: 4 },
  medium: { cpu: 4, memory: 8 },
  large: { cpu: 8, memory: 16 },
};

export interface PartitionStatus {
  name: string;
  max_nodes: number;
  active_nodes: number;
  idle_nodes: number;
  queue_depth: number;
  instance_type: string;
  use_spot: boolean;
}

export interface ClusterStatus {
  controller_status: string;
  partitions: PartitionStatus[];
  total_nodes: number;
  active_nodes: number;
  queue_depth: number;
  cost_burn_rate_hourly: number | null;
}

// BAL normalized compute types (Phase 12)

export interface NodePoolStatus {
  name: string;
  machine_type: string;
  min_nodes: number;
  max_nodes: number;
  current_nodes: number;
  status: string;
  spot?: boolean;
}

export interface InfraComputeStatus {
  controller_status: string;
  node_pools: NodePoolStatus[];
  total_nodes: number;
  active_nodes: number;
  queue_depth: number;
  health: string;
}

export interface NodePoolMetrics {
  name: string;
  cpu_utilization_pct: number;
  memory_utilization_pct: number;
  cost_rate_hourly: number;
}

export interface InfraComputeMetrics {
  cpu_utilization_pct: number;
  memory_utilization_pct: number;
  cost_burn_rate_hourly: number;
  node_pools: NodePoolMetrics[];
}

export interface SlurmJob {
  id: number;
  slurm_job_id: string;
  job_name: string | null;
  partition: string;
  status: SlurmJobStatus;
  user: UserSummary | null;
  experiment: ExperimentSummary | null;
  cpu_requested: number | null;
  memory_gb_requested: number | null;
  cpu_used: number | null;
  memory_gb_used: number | null;
  exit_code: number | null;
  cost_estimate: number | null;
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface SlurmJobListResponse {
  jobs: SlurmJob[];
  total: number;
  page: number;
  page_size: number;
}

export interface NotebookSession {
  id: number;
  session_type: SessionType;
  user: UserSummary | null;
  experiment: ExperimentSummary | null;
  resource_profile: ResourceProfile;
  cpu_cores: number;
  memory_gb: number;
  status: SessionStatus;
  idle_since: string | null;
  proxy_url: string | null;
  started_at: string | null;
  stopped_at: string | null;
  created_at: string;
  git_branch_name: string | null;
  git_commit_hash: string | null;
  environment_version_id: number | null;
}

export interface SessionListResponse {
  sessions: NotebookSession[];
  total: number;
}

export interface SessionLaunchRequest {
  session_type: SessionType;
  resource_profile: ResourceProfile;
  experiment_id?: number | null;
  image_uri?: string | null;
  input_file_ids?: number[];
  environment_version_id?: number | null;
}

export interface SessionProvenance {
  session_id: number;
  session_type: string;
  status: string;
  user: UserSummary | null;
  project_id: number | null;
  experiment_id: number | null;
  environment: {
    environment_id: number;
    environment_name: string;
    version_id: number;
    version_number: number;
    build_number: number;
    image_uri: string | null;
    definition_format: string;
  } | null;
  input_files: {
    id: number;
    filename: string;
    gcs_uri: string;
    file_type: string;
    size_bytes: number | null;
  }[];
  output_files: {
    id: number;
    filename: string;
    gcs_uri: string;
    file_type: string;
    size_bytes: number | null;
  }[];
  gcs_output_prefix: string | null;
  started_at: string | null;
  stopped_at: string | null;
}

export interface UserQuota {
  user_id: number;
  user_name: string | null;
  user_email: string | null;
  user_role: string | null;
  cpu_hours_limit: number | null;
  cpu_hours_used: number;
  quota_reset_at: string;
}

export interface QuotaUpdateRequest {
  cpu_hours_monthly_limit: number | null;
}

export interface BudgetInfo {
  monthly_budget: number | null;
  current_spend: number;
  projected_spend: number;
  threshold_alerts: string[];
}

// Phase 4 — Pipeline Orchestration

export type PipelineRunStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type PipelineProcessStatus = "pending" | "running" | "completed" | "failed" | "cached";

export interface ParameterSchema {
  definitions?: Record<string, {
    title?: string;
    properties?: Record<string, {
      type?: string;
      description?: string;
      default?: unknown;
      enum?: string[];
      hidden?: boolean;
      format?: string;
      minimum?: number;
      maximum?: number;
      fa_icon?: string;
    }>;
    required?: string[];
  }>;
}

export interface PipelineCatalog {
  id: number;
  pipeline_key: string;
  name: string;
  description: string | null;
  source_type: string;
  source_url: string | null;
  version: string | null;
  parameter_schema: ParameterSchema | null;
  default_params: Record<string, unknown> | null;
  is_builtin: boolean;
  enabled: boolean;
}

export interface PipelineCatalogListResponse {
  pipelines: PipelineCatalog[];
  total: number;
}

export interface PipelineProgress {
  total_processes: number;
  completed: number;
  running: number;
  failed: number;
  cached: number;
  percent_complete: number;
}

export interface PipelineProcess {
  id: number;
  process_name: string;
  task_id: string | null;
  status: PipelineProcessStatus;
  exit_code: number | null;
  cpu_usage: number | null;
  memory_peak_gb: number | null;
  duration_seconds: number | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface PipelineRun {
  id: number;
  pipeline_key: string | null;
  pipeline_name: string;
  pipeline_version: string | null;
  experiment: ExperimentSummary | null;
  submitted_by: UserSummary | null;
  status: PipelineRunStatus;
  parameters: Record<string, unknown> | null;
  input_files: Record<string, unknown> | null;
  output_files: Record<string, unknown> | null;
  progress: PipelineProgress | null;
  cost_estimate: number | null;
  error_message: string | null;
  work_dir: string | null;
  slurm_job_id: string | null;
  k8s_job_name: string | null;
  k8s_namespace: string | null;
  k8s_pod_name: string | null;
  actual_cost: number | null;
  reference_genome: string | null;
  alignment_algorithm: string | null;
  resume_from_run_id: number | null;
  review_verdict: ReviewVerdict | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

export interface PipelineRunDetail extends PipelineRun {
  processes: PipelineProcess[];
  samples: Array<{
    id: number;
    sample_id_unique: string | null;
    organism: string | null;
  }>;
}

export interface PipelineRunListResponse {
  runs: PipelineRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface PipelineRunLaunchRequest {
  pipeline_key: string;
  experiment_id: number;
  sample_ids?: number[] | null;
  parameters: Record<string, unknown>;
  resume_from_run_id?: number | null;
  reference_genome?: string | null;
  alignment_algorithm?: string | null;
}

export interface PipelineRunCompareResponse {
  runs: PipelineRun[];
  parameter_diffs: Record<string, Record<string, unknown>>;
}

export interface PipelineAddRequest {
  name: string;
  source_url: string;
  version?: string | null;
  description?: string | null;
}

// Phase 5 — Data Management + Visualization

export interface FileResponse {
  id: number;
  filename: string;
  gcs_uri: string;
  size_bytes: number | null;
  md5_checksum: string | null;
  file_type: string;
  tags: string[];
  uploader: UserSummary | null;
  project_id: number | null;
  experiment_id: number | null;
  sample_ids: number[];
  source_type: string;
  source_pipeline_run_id: number | null;
  source_notebook_session_id: number | null;
  storage_deleted: boolean;
  upload_timestamp: string;
  created_at: string;
}

export interface FileListResponse {
  files: FileResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface FileUploadInitiateResponse {
  upload_id: string;
  signed_url: string;
  gcs_uri: string;
}

export interface DatasetExperimentSummary {
  experiment_id: number;
  experiment_name: string;
  status: string;
  organism: string | null;
  tissue: string | null;
  molecule_type: string | null;
  instrument_model: string | null;
  review_status: string | null;
  sample_count: number;
  file_count: number;
  total_size_bytes: number;
  pipeline_run_count: number;
  has_qc_dashboard: boolean;
  has_cellxgene: boolean;
  owner: UserSummary | null;
  created_at: string;
}

export interface DatasetSearchResult {
  experiments: DatasetExperimentSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface DocumentResponse {
  id: number;
  title: string | null;
  file: FileResponse | null;
  has_extracted_text: boolean;
  linked_experiment_id: number | null;
  linked_sample_id: number | null;
  linked_pipeline_run_id: number | null;
  created_at: string;
}

export interface DocumentSearchResponse {
  documents: DocumentResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface BucketStats {
  bucket_name: string;
  total_bytes: number;
  object_count: number;
  by_storage_class: Record<string, number>;
  cost_estimate_monthly: number;
}

export interface LifecyclePolicyStatus {
  bucket_name: string;
  rules: Record<string, unknown>[];
  enabled: boolean;
}

export interface StorageDashboard {
  buckets: BucketStats[];
  total_bytes: number;
  total_cost_estimate_monthly: number;
  lifecycle_policies: LifecyclePolicyStatus[];
  last_updated: string;
}

export interface CellxgenePublishableFile {
  id: number;
  filename: string;
  gcs_uri: string;
  size_bytes: number | null;
  file_type: string;
  project_name: string | null;
  experiment_name: string | null;
  sample_names: string[];
  source_type: string;
  cellxgene_ready: boolean;
  cellxgene_status: string;
  created_at: string;
}

export interface CellxgeneFileInspection {
  embeddings: string[];
  cell_count: number;
  gene_count: number;
  cellxgene_ready: boolean;
  missing: string | null;
}

export interface CellxgenePublicationResponse {
  id: number;
  dataset_name: string;
  stable_url: string | null;
  access_url: string | null;
  status: string;
  file: FileResponse | null;
  experiment_id: number | null;
  published_by: UserSummary | null;
  published_at: string | null;
  created_at: string;
}

export interface QCMetrics {
  cell_count: number | null;
  median_reads_per_cell: number | null;
  median_genes_per_cell: number | null;
  median_umi_per_cell: number | null;
  mito_pct_median: number | null;
  doublet_score_median: number | null;
  saturation: number | null;
  // Sequencing metrics
  number_of_reads: number | null;
  valid_barcodes: number | null;
  q30_bases_barcode: number | null;
  q30_bases_rna_read: number | null;
  // Mapping metrics
  reads_mapped_genome: number | null;
  reads_mapped_genome_unique: number | null;
  // Mean values and totals
  mean_reads_per_cell: number | null;
  mean_umi_per_cell: number | null;
  mean_genes_per_cell: number | null;
  total_genes_detected: number | null;
  umis_in_cells: number | null;
  // Bulk/FastQC metrics
  total_sequences: number | null;
  percent_duplicates: number | null;
  percent_gc: number | null;
  avg_sequence_length: number | null;
  total_samples: number | null;
  quality_rating: string;
  // Chart data for interactive rendering
  barcode_rank_data: [number, number][] | null;
  chart_data: {
    star_alignment?: { name: string; value: number }[];
    base_quality?: [number, number][];
    gc_content?: { sample: [number, number][]; theoretical?: [number, number][] };
    duplication?: [number, number][];
  } | null;
}

export interface QCPlot {
  plot_type: string;
  title: string;
  file_id: number;
  download_url?: string | null;
}

export interface QCDashboardResponse {
  id: number;
  pipeline_run_id: number;
  experiment_id: number | null;
  metrics: QCMetrics;
  summary_text: string;
  plots: QCPlot[];
  status: string;
  generated_at: string | null;
  created_at: string;
}

export interface QCDashboardSummary {
  id: number;
  pipeline_run_id: number;
  quality_rating: string;
  cell_count: number | null;
  status: string;
  generated_at: string | null;
}

export interface PlotArchiveResponse {
  id: number;
  title: string | null;
  file: FileResponse | null;
  experiment_id: number | null;
  pipeline_run_id: number | null;
  notebook_session_id: number | null;
  tags: string[];
  thumbnail_url: string | null;
  indexed_at: string;
}

export interface PlotArchiveListResponse {
  plots: PlotArchiveResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface SearchHit {
  entity_type: string;
  entity_id: number;
  title: string;
  snippet: string | null;
  experiment_id: number | null;
  relevance_score: number | null;
}

export interface SearchResult {
  results: SearchHit[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProvenanceNode {
  entity_type: string;
  entity_id: number;
  label: string;
  timestamp: string | null;
  children: number[];
  metadata: Record<string, unknown>;
}

export interface ProvenanceChain {
  experiment: ProvenanceNode;
  samples: ProvenanceNode[];
  fastq_uploads: ProvenanceNode[];
  pipeline_runs: ProvenanceNode[];
  outputs: ProvenanceNode[];
  cellxgene_publications: ProvenanceNode[];
  qc_dashboards: ProvenanceNode[];
}

// Phase 6 — GitOps + Package Management

export interface GitOpsRepoStatus {
  initialized: boolean;
  repo_url: string | null;
  repo_name: string | null;
  last_commit_sha: string | null;
  last_commit_at: string | null;
  status: string;
}

export interface GitCommit {
  sha: string;
  message: string;
  author: string;
  timestamp: string;
  files_changed: number | null;
}

export interface GitCommitDetail extends GitCommit {
  diff: string;
  files: string[];
}

export interface GitCommitListResponse {
  commits: GitCommit[];
  total: number;
  page: number;
  page_size: number;
}

export interface PackageSearchResult {
  name: string;
  version: string;
  description: string | null;
  source: string;
  channel: string | null;
  homepage: string | null;
}

export interface PackageSearchResponse {
  results: PackageSearchResult[];
  total: number;
  query: string;
}

export interface DependencyNode {
  name: string;
  version: string;
  source: string;
  action: string;
}

export interface DependencyTree {
  package: string;
  version: string;
  dependencies: DependencyNode[];
  total_new_packages: number;
  estimated_disk_bytes: number | null;
}

/** @deprecated Legacy type from pre-ADR-033 package tracking */
export interface InstalledPackage {
  name: string;
  version: string | null;
  source: string;
  pinned: boolean;
  installed_at: string;
}

// --- Versioned Environments (ADR-033) ---

export interface EnvironmentVersionSummary {
  id: number;
  version_number: number;
  build_number: number;
  status: "draft" | "building" | "ready" | "failed";
  definition_format: "dockerfile" | "conda";
  image_uri: string | null;
  created_at: string;
}

export interface EnvironmentResponse {
  id: number;
  name: string;
  description: string | null;
  visibility: "team" | "organization";
  version_count: number;
  latest_version: EnvironmentVersionSummary | null;
  created_by: UserSummary | null;
  created_at: string;
  updated_at: string;
}

export interface EnvironmentListResponse {
  environments: EnvironmentResponse[];
  total: number;
}

export interface EnvironmentDetailResponse {
  id: number;
  name: string;
  description: string | null;
  visibility: "team" | "organization";
  versions: EnvironmentVersionSummary[];
  created_by: UserSummary | null;
  created_at: string;
  updated_at: string;
}

export interface EnvironmentVersionResponse {
  id: number;
  environment_id: number;
  version_number: number;
  status: "draft" | "building" | "ready" | "failed";
  definition_format: "dockerfile" | "conda";
  definition_content: string;
  build_id: string | null;
  image_uri: string | null;
  created_by: UserSummary | null;
  created_at: string;
}

export interface BuildLogsResponse {
  build_id: string | null;
  status: string;
  logs_url: string | null;
}

export interface EnvironmentCreateRequest {
  name: string;
  description?: string;
  visibility?: "team" | "organization";
}

export interface VersionCreateRequest {
  definition_format: "dockerfile" | "conda";
  definition_content: string;
}

export interface TemplateNotebookResponse {
  id: number;
  name: string;
  description: string | null;
  category: string | null;
  compatible_with: string | null;
  parameters: Record<string, unknown>;
  is_builtin: boolean;
  created_at: string;
}

export interface TemplateNotebookListResponse {
  notebooks: TemplateNotebookResponse[];
  total: number;
}

// Phase 8 — MINSEQE Metadata + Pipeline Reviews

export type ReviewVerdict = "approved" | "approved_with_caveats" | "rejected" | "revision_requested";

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

export interface SampleVerdictEntry {
  sample_id: number;
  verdict: ReviewVerdict;
  notes: string | null;
}

export interface PipelineRunReview {
  id: number;
  pipeline_run_id: number;
  reviewer: UserSummary;
  verdict: ReviewVerdict;
  notes: string | null;
  sample_verdicts: SampleVerdictEntry[] | null;
  recommended_exclusions: number[] | null;
  is_active: boolean;
  reviewed_at: string;
  created_at: string;
}

export interface PipelineRunReviewListResponse {
  reviews: PipelineRunReview[];
  total: number;
}

// Reference Data types (Phase 9)
export interface ReferenceDataset {
  id: number;
  organization_id: number;
  name: string;
  category: string;
  scope: string;
  version: string;
  source_url: string | null;
  gcs_prefix: string;
  total_size_bytes: number | null;
  file_count: number | null;
  status: "active" | "deprecated" | "pending_approval";
  deprecation_note: string | null;
  superseded_by_id: number | null;
  created_at: string;
}

export interface ReferenceDatasetFile {
  id: number;
  filename: string;
  gcs_uri: string;
  size_bytes: number | null;
  md5_checksum: string | null;
  file_type: string | null;
  created_at: string;
}

export interface ReferenceDatasetDetail extends ReferenceDataset {
  files: ReferenceDatasetFile[];
  uploaded_by: { id: number; name: string | null; email: string } | null;
  approved_by: { id: number; name: string | null; email: string } | null;
}

export interface ReferenceDatasetListResponse {
  references: ReferenceDataset[];
  total: number;
}

export interface ImpactPipelineRun {
  pipeline_run_id: number;
  pipeline_name: string;
  pipeline_version: string | null;
  experiment_id: number | null;
  experiment_name: string | null;
  status: string;
  review_verdict: string | null;
  completed_at: string | null;
}

export interface ImpactSummary {
  reference_dataset_id: number;
  total_pipeline_runs: number;
  total_experiments: number;
  pipeline_runs: ImpactPipelineRun[];
}

export interface GeoValidationField {
  geo_column: string;
  status: "complete" | "populated_unvalidated" | "missing_required" | "missing_recommended";
  value: string | null;
  message: string | null;
}

export interface GeoSampleValidation {
  sample_id: number;
  sample_name: string;
  fields: GeoValidationField[];
}

export interface GeoValidationSummary {
  total_fields: number;
  complete: number;
  populated_unvalidated: number;
  missing_required: number;
  missing_recommended: number;
}

export interface GeoValidationReport {
  experiment_id: number;
  pipeline_run_id: number | null;
  series_fields: GeoValidationField[];
  sample_validations: GeoSampleValidation[];
  protocol_fields: GeoValidationField[];
  summary: GeoValidationSummary;
}

// Analysis Snapshots

export interface AnalysisSnapshot {
  id: number;
  experiment_id: number | null;
  project_id: number | null;
  notebook_session_id: number | null;
  user_id: number;
  user_name: string;
  label: string;
  notes: string | null;
  object_type: string;
  cell_count: number | null;
  gene_count: number | null;
  cluster_count: number | null;
  starred: boolean;
  figure_url: string | null;
  created_at: string;
}

export interface AnalysisSnapshotDetail extends AnalysisSnapshot {
  parameters_json: Record<string, unknown> | null;
  embeddings_json: Record<string, { n_components: number }> | null;
  clusterings_json: Record<string, { n_clusters: number; distribution: Record<string, number> }> | null;
  layers_json: string[] | null;
  metadata_columns_json: string[] | null;
  command_log_json: Array<{ name: string; params: Record<string, unknown> }> | null;
  checkpoint_url: string | null;
}

export interface ParameterDiff {
  parameter_path: string;
  values: Record<number, unknown>;
  changed: boolean;
}

export interface EmbeddingDiff {
  embedding_name: string;
  dimensions: Record<number, number | null>;
  present_in: number[];
}

export interface ClusteringDiff {
  clustering_name: string;
  n_clusters: Record<number, number>;
  distributions: Record<number, Record<string, number>>;
  present_in: number[];
}

export interface CommandDiff {
  command_name: string;
  present_in: number[];
  params_differ: boolean;
  params: Record<number, Record<string, unknown>> | null;
}

export interface CellCountPoint {
  snapshot_id: number;
  label: string;
  cell_count: number;
  created_at: string;
}

export interface SnapshotComparison {
  snapshots: AnalysisSnapshotDetail[];
  parameter_diff: ParameterDiff[];
  embedding_diff: EmbeddingDiff[];
  clustering_diff: ClusteringDiff[];
  command_log_diff: CommandDiff[] | null;
  cell_count_series: CellCountPoint[];
}

// Phase 13 — Auto-Ingest, Naming Profiles, Pipeline Triggers

export interface SegmentDefinition {
  position: number;
  field: "date" | "project_code" | "experiment_code" | "sample_id" | "sample_index" | "data_type" | "analysis_type" | "researcher_initials" | "version" | "organism" | "ignore" | "custom";
  format?: string | null;
  required: boolean;
  custom_label?: string | null;
}

export interface NamingProfile {
  id: number;
  organization_id: number;
  name: string;
  description: string | null;
  delimiter: string;
  strip_extension: boolean;
  segments: SegmentDefinition[];
  project_code_mappings: Record<string, string>;
  experiment_code_mappings: Record<string, string>;
  status: "active" | "inactive";
  created_at: string;
  updated_at: string;
}

export interface NamingProfileTestResult {
  filename: string;
  match_status: "matched" | "unmatched" | "multiple_matches";
  profile_id: number | null;
  profile_name: string | null;
  parsed_segments: Record<string, string> | null;
  candidate_profile_ids: number[];
  error: string | null;
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

// Experiment Auto-Run

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
  created_at: string;
  updated_at: string;
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
  created_at: string;
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

// Work Nodes (ADR-034)

export interface WorkNode {
  id: number;
  session_type: string;
  user: UserSummary | null;
  project_id: number | null;
  environment_version_id: number | null;
  machine_type: string | null;
  data_mount_paths: string[] | null;
  resource_profile: string;
  cpu_cores: number;
  memory_gb: number;
  status: string;
  access_url: string | null;
  heartbeat_at: string | null;
  started_at: string | null;
  stopped_at: string | null;
  created_at: string;
}

export interface WorkNodeListResponse {
  sessions: WorkNode[];
  total: number;
}

export interface WorkNodeLaunchRequest {
  project_id: number;
  environment_version_id: number;
  machine_type: string;
  data_mount_paths?: string[];
}

export interface MachineType {
  name: string;
  category: string;
  cpu: number;
  memory_gb: number;
  gpu: string | null;
  description: string;
}

export interface DataMount {
  path: string;
  label: string;
  description: string;
}
