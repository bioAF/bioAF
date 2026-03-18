variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the BigQuery dataset"
  type        = string
  default     = "us-central1"
}

variable "backend_service_account_email" {
  description = "Email of the GCP service account the backend runs as. Defaults to the project's default compute SA."
  type        = string
  default     = ""
}
