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
  description = "GCP zone for the zonal GKE cluster"
}

variable "org_slug" {
  type        = string
  description = "Organization slug used in resource naming"
}

variable "stack_uid" {
  type        = string
  description = "Short unique ID appended to resource names to avoid GCP soft-delete conflicts on redeploy"
}

variable "k8s_pipeline_machine_type" {
  type        = string
  default     = "n2-highmem-8"
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
