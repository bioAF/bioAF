output "cluster_name" {
  value       = google_container_cluster.bioaf.name
  description = "Name of the GKE cluster"
}

output "cluster_endpoint" {
  value       = google_container_cluster.bioaf.endpoint
  description = "GKE cluster API endpoint URL"
}

output "cluster_ca_cert" {
  value       = google_container_cluster.bioaf.master_auth[0].cluster_ca_certificate
  description = "GKE cluster CA certificate (base64)"
  sensitive   = true
}

# plan_7 step 16a. Read into platform_config the same way the notebook runner's is, so the backend
# can tell an install that HAS an isolated identity for untrusted code from one that does not. There
# is no fallback: without this, running a paper's own code is refused.
output "untrusted_runner_sa_email" {
  description = "Email of the untrusted-execution service account (plan_7 step 16a)"
  value       = google_service_account.untrusted_runner.email
}

output "notebook_runner_sa_email" {
  value       = google_service_account.notebook_runner.email
  description = "GCP service account email for notebook pod Workload Identity"
}

output "cellxgene_runner_sa_email" {
  value       = google_service_account.cellxgene_runner.email
  description = "GCP service account email for cellxgene pod Workload Identity"
}
