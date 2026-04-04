output "state_bucket_name" {
  description = "Name of the GCS bucket holding Terraform state"
  value       = google_storage_bucket.terraform_state.name
}

output "state_bucket_url" {
  description = "gs:// URL for the Terraform state bucket"
  value       = "gs://${google_storage_bucket.terraform_state.name}"
}

output "backups_bucket_name" {
  description = "Name of the persistent backups GCS bucket"
  value       = google_storage_bucket.backups.name
}
