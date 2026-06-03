# =============================================================================
# Meilisearch (optional, enable_meilisearch)
# Deployed on GKE — Terraform manages service account and persistent disk
# =============================================================================

resource "google_service_account" "meilisearch" {
  count        = var.enable_meilisearch ? 1 : 0
  account_id   = "bioaf-meilisearch"
  display_name = "bioAF Meilisearch"
}

resource "google_service_account_iam_member" "meilisearch_workload_identity" {
  count              = var.enable_meilisearch ? 1 : 0
  service_account_id = google_service_account.meilisearch[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[bioaf-meilisearch/meilisearch]"
}

resource "google_compute_disk" "meilisearch_data" {
  count = var.enable_meilisearch ? 1 : 0
  name  = "bioaf-meilisearch-data"
  type  = "pd-standard"
  zone  = var.zone
  size  = 20

  labels = {
    component   = "meilisearch"
    environment = var.environment
  }
}

# GKE namespace for Meilisearch
resource "kubernetes_namespace" "meilisearch" {
  count = var.enable_meilisearch ? 1 : 0

  metadata {
    name = "bioaf-meilisearch"
    labels = {
      component   = "meilisearch"
      environment = var.environment
    }
  }
}

# Meilisearch master key stored as K8s secret
resource "kubernetes_secret" "meilisearch_master_key" {
  count = var.enable_meilisearch ? 1 : 0

  metadata {
    name      = "meilisearch-master-key"
    namespace = kubernetes_namespace.meilisearch[0].metadata[0].name
  }

  data = {
    MEILI_MASTER_KEY = var.meilisearch_master_key
  }
}

variable "meilisearch_master_key" {
  description = "Meilisearch master key for API authentication"
  type        = string
  sensitive   = true
  default     = ""
}
