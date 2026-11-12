// Notebook sessions, environments, packages, analysis snapshots and work nodes.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { components } from "../../../contracts/internal-api";
import type { ExperimentReference, ProjectSummary } from "./experiments";
import type { UserSummary } from "./identity";

export type SessionStatus = "pending" | "starting" | "running" | "idle" | "stopping" | "stopped" | "failed";

export type SessionType = "jupyter" | "rstudio";

export type ResourceProfile = "small" | "medium" | "large" | "xlarge" | "2xlarge";

// Keep in sync with RESOURCE_PROFILES in backend/app/services/notebook_service.py.
export const RESOURCE_PROFILES: Record<ResourceProfile, { cpu: number; memory: number }> = {
  small: { cpu: 2, memory: 8 },
  medium: { cpu: 4, memory: 16 },
  large: { cpu: 8, memory: 32 },
  xlarge: { cpu: 16, memory: 64 },
  "2xlarge": { cpu: 16, memory: 128 },
};

/**
 * A notebook session as the session API emits it. Generated transport: `status`, `session_type`
 * and `resource_profile` are open strings on the wire; narrow them explicitly (see
 * `lib/sessionRecreate.ts`) rather than asserting the unions above.
 */
export type NotebookSession = components["schemas"]["SessionResponse"];

export type SessionListResponse = components["schemas"]["SessionListResponse"];

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
    storage_uri: string;
    file_type: string;
    size_bytes: number | null;
  }[];
  output_files: {
    id: number;
    filename: string;
    gcs_uri: string;
    storage_uri: string;
    file_type: string;
    size_bytes: number | null;
  }[];
  gcs_output_prefix: string | null;
  started_at: string | null;
  stopped_at: string | null;
}

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

export interface EnvironmentVersionSummary {
  id: number;
  version_number: number;
  build_number: number;
  status: "draft" | "building" | "ready" | "failed";
  definition_format: "dockerfile" | "conda";
  image_uri: string | null;
  created_at: string | null;
}

export interface EnvironmentResponse {
  id: number;
  name: string;
  description: string | null;
  visibility: "team" | "organization";
  environment_type: "notebook" | "work_node" | "pipeline";
  version_count: number;
  latest_version: EnvironmentVersionSummary | null;
  created_by: UserSummary | null;
  created_at: string | null;
  updated_at: string | null;
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
  environment_type: "notebook" | "work_node" | "pipeline";
  versions: EnvironmentVersionSummary[];
  created_by: UserSummary | null;
  created_at: string | null;
  updated_at: string | null;
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
  created_at: string | null;
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
  environment_type?: "notebook" | "work_node" | "pipeline";
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

export interface WorkNode {
  id: number;
  session_type: string;
  user: UserSummary | null;
  project_id: number | null;
  project: ProjectSummary | null;
  environment_version_id: number | null;
  machine_type: string | null;
  input_file_ids: number[] | null;
  resource_profile: string;
  cpu_cores: number;
  memory_gb: number;
  requested_disk_gb: number | null;
  status: string;
  failure_reason: string | null;
  failure_message: string | null;
  access_url: string | null;
  gce_instance_name: string | null;
  gce_zone: string | null;
  github_repo_ids: number[] | null;
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
  project_id?: number;
  experiment_id?: number;
  environment_version_id: number;
  machine_type: string;
  input_file_ids?: number[];
  github_repo_ids?: number[];
}

export interface GitHubRepo {
  id: number;
  git_ssh_url: string;
  display_name: string;
  created_at: string;
}

export interface GitHubRepoListResponse {
  repos: GitHubRepo[];
  total: number;
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
