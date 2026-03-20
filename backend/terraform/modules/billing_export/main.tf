terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  backend "gcs" {} # Configured dynamically by the executor
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_bigquery_dataset" "billing_export" {
  dataset_id  = "billing_export"
  project     = var.project_id
  location    = "US"
  description = "BigQuery billing export dataset managed by bioAF"

  labels = {
    managed_by = "bioaf"
    purpose    = "billing-export"
  }
}

# Grant the backend service account read access to the billing dataset
data "google_project" "current" {
  project_id = var.project_id
}

locals {
  _backend_sa = var.backend_service_account_email != "" ? var.backend_service_account_email : "${data.google_project.current.number}-compute@developer.gserviceaccount.com"
  backend_sa  = "serviceAccount:${local._backend_sa}"
}

resource "google_bigquery_dataset_iam_member" "backend_data_viewer" {
  dataset_id = google_bigquery_dataset.billing_export.dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataViewer"
  member     = local.backend_sa
}

# The SA also needs bigquery.jobUser at project level to run queries
resource "google_project_iam_member" "backend_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = local.backend_sa
}
