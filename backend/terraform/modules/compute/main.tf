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
  location = var.zone # Zonal cluster (cheaper than regional for POC)

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
  name     = "bioaf-pipelines"
  cluster  = google_container_cluster.bioaf.id
  project  = var.project_id
  location = var.zone

  autoscaling {
    min_node_count = 0
    max_node_count = var.k8s_pipeline_max_nodes
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

    taint {
      key    = "bioaf.io/pool"
      value  = "pipelines"
      effect = "NO_SCHEDULE"
    }
  }
}

# --- Interactive Node Pool ---

resource "google_container_node_pool" "interactive" {
  name     = "bioaf-interactive"
  cluster  = google_container_cluster.bioaf.id
  project  = var.project_id
  location = var.zone

  autoscaling {
    min_node_count = 0
    max_node_count = var.k8s_interactive_max_nodes
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
