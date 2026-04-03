terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  backend "gcs" {}
}

# --- GKE Cluster ---

resource "google_container_cluster" "bioaf" {
  name     = "bioaf-${var.org_slug}-${var.stack_uid}"
  project  = var.project_id
  location = var.region

  # Terraform-managed lifecycle -- teardown handles deletion
  deletion_protection      = false
  remove_default_node_pool = true
  initial_node_count       = 1

  # Workload Identity for pod-level GCP auth
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Network policy for pod isolation
  network_policy {
    enabled = true
  }

  resource_labels = {
    managed_by = "bioaf"
    org        = var.org_slug
  }
}

# --- Pipeline Node Pool ---

resource "google_container_node_pool" "pipelines" {
  name           = "bioaf-pipelines"
  cluster        = google_container_cluster.bioaf.id
  project        = var.project_id
  location       = var.region
  node_locations = var.k8s_node_zones

  autoscaling {
    min_node_count  = 0
    max_node_count  = var.k8s_pipeline_max_nodes
    location_policy = "ANY"
  }

  node_config {
    machine_type = var.k8s_pipeline_machine_type
    spot         = var.k8s_pipeline_use_spot

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = {
      "bioaf.io/pool" = "pipelines"
    }

    # No taint on pipelines pool: Nextflow K8s executor spawns process
    # pods that cannot carry custom tolerations, so an untainted pool is
    # required. The label + nodeSelector on the head Job still directs
    # orchestrator pods here; other pools' taints prevent Nextflow
    # process pods from landing elsewhere.
  }
}

# --- Interactive Node Pool ---

resource "google_container_node_pool" "interactive" {
  name           = "bioaf-interactive"
  cluster        = google_container_cluster.bioaf.id
  project        = var.project_id
  location       = var.region
  node_locations = var.k8s_node_zones

  autoscaling {
    min_node_count  = 0
    max_node_count  = var.k8s_interactive_max_nodes
    location_policy = "ANY"
  }

  node_config {
    machine_type = var.k8s_interactive_machine_type
    spot         = false # On-demand for notebook sessions

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = {
      "bioaf.io/pool" = "interactive"
    }

    taint {
      key    = "bioaf.io/pool"
      value  = "interactive"
      effect = "NO_SCHEDULE"
    }
  }
}

# --- IAM binding for GCS access from GKE nodes ---

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_iam_member" "gke_storage_access" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "gke_default_node_sa" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "gke_artifact_registry_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

# --- Workload Identity for notebook pods ---
#
# With Workload Identity enabled, pods cannot use the node's default SA.
# Create a dedicated GCP SA for notebook workloads, grant it GCS access,
# and bind it to the bioaf-notebook-runner K8s SA so pods get credentials
# via the metadata server.

resource "google_service_account" "notebook_runner" {
  project      = var.project_id
  account_id   = "bioaf-notebook-runner"
  display_name = "bioAF Notebook Runner"
  description  = "GCP service account for notebook session pods (Workload Identity)"
}

resource "google_project_iam_member" "notebook_runner_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.notebook_runner.email}"
}

resource "google_service_account_iam_member" "notebook_runner_workload_identity" {
  service_account_id = google_service_account.notebook_runner.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[bioaf-notebooks/bioaf-notebook-runner]"
}
