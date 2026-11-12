// QC dashboards, plot archive and reference data.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { FileResponse } from "./files";

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

export interface QCMetricSpec {
  label: string;
  format?: string;
  thresholds?: { good?: string; warn?: string };
}

export interface QCSection {
  id: string;
  title?: string | null;
  layout?: string | null;
  metrics: string[];
}

export interface QCChartSpec {
  type: string;
  metric_key?: string | null;
  title?: string | null;
}

export interface QCPlotSpec {
  file_glob: string;
  title: string;
  type: string;
}

export interface QCDashboardConfig {
  template: string;
  sections: QCSection[];
  metrics: Record<string, QCMetricSpec>;
  charts: QCChartSpec[];
  plots: QCPlotSpec[];
}

export interface QCDashboardResponse {
  id: number;
  pipeline_run_id: number;
  experiment_id: number | null;
  pipeline_name: string | null;
  pipeline_version: string | null;
  project_name: string | null;
  experiment_name: string | null;
  metrics: QCMetrics;
  raw_metrics: Record<string, unknown>;
  qc_config: QCDashboardConfig;
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
  project_name: string | null;
  experiment_name: string | null;
  pipeline_name: string | null;
  pipeline_version: string | null;
  sample_external_ids: string[];
}

export interface PlotArchiveResponse {
  id: number;
  title: string | null;
  file: FileResponse | null;
  experiment_id: number | null;
  experiment_name: string | null;
  project_name: string | null;
  pipeline_run_id: number | null;
  pipeline_run_name: string | null;
  notebook_session_id: number | null;
  notebook_session_type: string | null;
  source_type: string | null;
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
  status: "uploading" | "active" | "deprecated" | "pending_approval" | "failed";
  deprecation_note: string | null;
  superseded_by_id: number | null;
  created_at: string;
}

export interface ReferenceDatasetFile {
  id: number;
  filename: string;
  gcs_uri: string;
  storage_uri: string;
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

export interface ReferenceUploadFileSpec {
  filename: string;
  size_bytes: number;
  content_type?: string;
  md5_checksum?: string;
}

export interface ReferenceUploadInitRequest {
  name: string;
  category: string;
  scope: string;
  version: string;
  source_url?: string;
  description?: string;
  files: ReferenceUploadFileSpec[];
}

export interface ReferenceUploadSlot {
  filename: string;
  session_url: string;
  expires_at: string;
}

export interface ReferenceUploadInitResponse {
  reference_id: number;
  gcs_prefix: string;
  uploads: ReferenceUploadSlot[];
}

export interface ReferenceImportRequest {
  name: string;
  category: string;
  scope: string;
  version: string;
  source_url: string;
  source_md5_url?: string;
  auth_header?: string;
  extract: "none" | "gzip" | "tar" | "tar.gz";
  expected_files?: string[];
  description?: string;
}

export interface ReferenceImportStartResponse {
  reference_id: number;
  import_job_id: string;
  status: string;
}

export interface ReferenceImportStatusResponse {
  reference_id: number;
  status: string;
  progress_pct: number | null;
  bytes_downloaded: number | null;
  total_bytes: number | null;
  error_message: string | null;
  import_job_id: string | null;
  updated_at: string | null;
}
