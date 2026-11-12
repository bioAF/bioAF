// Files, storage, datasets, documents, cellxgene publishing and GEO validation.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { UserSummary } from "./identity";

export interface PipelineRunRef {
  id: number;
  pipeline_name: string;
  launcher: UserSummary | null;
}

export interface ComputeSessionRef {
  id: number;
  // "work_node" for SSH/GCE sessions; "notebook" for in-browser sessions
  kind: "work_node" | "notebook" | string;
  // For notebooks only: "rstudio" or "jupyter"; null for work nodes
  notebook_type: "rstudio" | "jupyter" | string | null;
  launcher: UserSummary | null;
}

export interface FileProvenance {
  project_id: number | null;
  project_name: string | null;
  experiment_id: number | null;
  experiment_name: string | null;
  sample_labels: string[];
  pipeline_run: PipelineRunRef | null;
  compute_session: ComputeSessionRef | null;
  creator: UserSummary | null;
}

export interface FileResponse {
  id: number;
  filename: string;
  gcs_uri: string;
  storage_uri: string;
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
  is_global?: boolean;
  upload_timestamp: string;
  created_at: string;
  provenance?: FileProvenance | null;
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
  storage_uri: string;
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
  storage_uri: string;
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
