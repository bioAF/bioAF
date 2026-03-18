output "dataset_id" {
  description = "BigQuery dataset ID for billing export"
  value       = google_bigquery_dataset.billing_export.dataset_id
}

output "dataset_project" {
  description = "GCP project containing the billing export dataset"
  value       = var.project_id
}
