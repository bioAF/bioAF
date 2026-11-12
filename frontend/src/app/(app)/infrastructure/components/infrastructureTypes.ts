import type { QuotaVerdict } from "./clusterQuotaVerdict";

/** What the infrastructure components page reads from the stack endpoints. */

export interface TerraformStatus {
  terraform_initialized: boolean;
  terraform_state_bucket: string;
  gcp_credentials_configured: boolean;
  active_run_id: number | null;
  active_run_status: string | null;
}

export interface TerraformRun {
  id: number;
  action: string;
  module_name: string | null;
  status: string;
  resources_planned: number | null;
  resources_completed: number;
  triggered_by_user_id: number;
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
  plan_json: object | null;
  terraform_state_url: string | null;
}

export interface NodePoolInfo {
  name: string;
  machine_type: string;
  min_nodes: number;
  max_nodes: number;
  current_nodes: number;
  spot: boolean;
  status: string;
}

export interface ClusterInfo {
  cluster_name: string;
  status: string;
  node_count: number;
  pipeline_pool: NodePoolInfo;
  interactive_pool: NodePoolInfo;
}

export interface StackStatus {
  compute_stack: string | null;
  compute_deployed: boolean;
  storage_deployed: boolean;
  pubsub_configured: boolean;
  cluster: ClusterInfo | null;
  has_orphaned_clusters: boolean;
}

export interface ComponentDef {
  key: string;
  name: string;
  category: string;
  description: string;
  cost_estimate: string;
  dependencies: string[];
  status: string;
  configurable: boolean;
}

export interface ComponentsData {
  compute_stack: string | null;
  compute_deployed: boolean;
  storage_deployed: boolean;
  components: ComponentDef[];
}

export interface ClusterConfig {
  k8s_pipeline_machine_type: string;
  k8s_pipeline_max_nodes: number;
  k8s_pipeline_use_spot: boolean;
  k8s_pipeline_disk_size_gb: number;
  k8s_pipeline_disk_type: string;
  k8s_interactive_machine_type: string;
  k8s_interactive_max_nodes: number;
  // What the CURRENT pool can build. Terraform reporting success and GKE reporting
  // RUNNING were both true of a pool with zero capacity; this is what tells them apart.
  pipeline_pool_quota?: QuotaVerdict | null;
}
