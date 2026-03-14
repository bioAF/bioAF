variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "org_slug" {
  description = "Organization slug used in bucket naming"
  type        = string
}

variable "stack_uid" {
  description = "Short unique ID appended to resource names to avoid GCP soft-delete conflicts on redeploy"
  type        = string
}
