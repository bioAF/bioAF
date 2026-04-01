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
  description = "Short unique ID appended to resource names to avoid GCP soft-delete conflicts on redeploy. Defaults to 'pending' so terraform destroy works from state without requiring the original value."
  type        = string
  default     = "pending"
}

variable "backend_service_account_email" {
  description = "Email of the GCP service account the backend runs as. When provided, objectAdmin is granted on all storage buckets. Defaults to the project's default compute SA."
  type        = string
  default     = ""
}
