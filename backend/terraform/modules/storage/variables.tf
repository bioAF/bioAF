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
  description = "Short unique ID (the storage generation) appended to resource names and recorded as the storage_uid label, so each deployment has its own names. Defaults to 'pending' so terraform destroy works from state without requiring the original value."
  type        = string
  default     = "pending"
}

variable "backend_service_account_email" {
  description = "Email of the GCP service account the backend runs as. When provided, objectAdmin is granted on all storage buckets. Defaults to the project's default compute SA."
  type        = string
  default     = ""
}

variable "bioaf_app_sa_email" {
  description = "Email of the bioaf-app SA (SA hardening). When provided, per-subscription roles/pubsub.subscriber is granted on each bioAF-managed Pub/Sub subscription."
  type        = string
  default     = ""
}

variable "bucket_markers" {
  description = "bioAF's identity marker per store (written as the bioaf_marker label). bioAF records each marker with the bucket's identity, so a bucket deleted and recreated under the same name is told apart. Empty: no marker is written."
  type        = map(string)
  default     = {}
}
