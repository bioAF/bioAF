variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  description = "GCP region"
}

variable "zone" {
  type        = string
  description = "GCP zone (retained for backward compatibility with tfvars generation)"
}

variable "org_slug" {
  type        = string
  description = "Organization slug used in resource naming"
}

variable "stack_uid" {
  type        = string
  description = "Short unique ID appended to resource names to avoid GCP soft-delete conflicts on redeploy. Defaults to 'pending' so terraform destroy works from state without requiring the original value."
  default     = "pending"
}

variable "k8s_node_zones" {
  type        = list(string)
  default     = []
  description = "Additional zones for node pool placement. When empty, uses the cluster zone only. Set to multiple zones (e.g. [\"us-central1-a\",\"us-central1-b\",\"us-central1-c\"]) so the autoscaler can fall back to another zone when a machine type is unavailable."
}

variable "k8s_pipeline_machine_type" {
  type        = string
  default     = "n2-highmem-16"
  description = "Machine type for the pipeline node pool"
}

variable "k8s_pipeline_max_nodes" {
  type        = number
  default     = 20
  description = "Maximum number of nodes in the pipeline autoscaler"
}

variable "k8s_pipeline_use_spot" {
  type        = bool
  default     = true
  description = "Whether the pipeline pool uses spot instances"
}

variable "k8s_interactive_machine_type" {
  type        = string
  default     = "n2-standard-4"
  description = "Machine type for the interactive node pool"
}

variable "k8s_interactive_max_nodes" {
  type        = number
  default     = 5
  description = "Maximum number of nodes in the interactive autoscaler"
}

variable "k8s_system_machine_type" {
  type        = string
  default     = "e2-standard-2"
  description = "Machine type for the always-on system node pool that hosts GKE addons (calico-typha, fluentbit, gmp-operator, gke-metadata-server, etc.). e2-standard-2 has 2 dedicated vCPU and 8 GiB RAM (~1.9 CPU / ~7 GiB allocatable) -- enough headroom that one node per zone absorbs the full addon DaemonSet set. Avoid shared-core burstable types (e2-small, e2-medium): Kubernetes only sees the 940m burstable max as allocatable on either, so the autoscaler packs them identically and pins to max=2 per zone. Still small enough that Nextflow process pods cannot fit and fall back to the pipelines pool."
}

variable "k8s_system_max_nodes" {
  type        = number
  default     = 2
  description = "Maximum number of nodes in the system pool autoscaler. Min is hardcoded at 1 -- the pool must always have a home for system addons."
}

variable "bioaf_bootstrap_sa_email" {
  type        = string
  default     = ""
  description = "Email of the bioaf-bootstrap SA. When set, attaches the bioaf-managed=true Resource Manager tag to the GKE cluster so bioaf-app's roles/container.admin tag-condition resolves."
}
