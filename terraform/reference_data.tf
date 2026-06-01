# =============================================================================
# Reference data URL importer
#
# A GKE Job submitted by the bioAF backend streams a public URL (GENCODE,
# Ensembl, 10x reference packages, etc.) directly into the org's
# references GCS bucket. The Pod runs the backend image with
# `python -m app.workers.reference_importer` and authenticates to GCS via
# Workload Identity through the bioaf-reference-importer KSA.
#
# Terraform owns the GCP-side identity (GSA + WI binding). The KSA is
# created at runtime by the backend in the bioaf-pipelines namespace; see
# backend/app/adapters/compute/kubernetes.py.
# =============================================================================

resource "google_service_account" "reference_importer" {
  account_id   = "bioaf-reference-importer"
  display_name = "bioAF Reference Data Importer"
}

# Project-wide GCS object admin: the importer writes to the references
# bucket (named via platform_config and not managed by Terraform). Scoped
# to storage roles only, matching the nextflow / snakemake pattern in
# pipelines.tf.
resource "google_project_iam_member" "reference_importer_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.reference_importer.email}"
}

resource "google_project_iam_member" "reference_importer_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.reference_importer.email}"
}

# Workload Identity: bind the bioaf-pipelines/bioaf-reference-importer KSA
# to the GSA above. The KSA itself is created at runtime by the backend.
resource "google_service_account_iam_member" "reference_importer_workload_identity" {
  service_account_id = google_service_account.reference_importer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[bioaf-pipelines/bioaf-reference-importer]"
}

output "reference_importer_service_account_email" {
  description = "Email of the GSA that the bioaf-reference-importer KSA impersonates via Workload Identity."
  value       = google_service_account.reference_importer.email
}
